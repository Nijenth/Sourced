"""FastAPI entry point for the authenticated Sourced API."""

import logging
from contextlib import asynccontextmanager
from os import environ

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from starlette.middleware.cors import CORSMiddleware

from rag.config import load_settings
from rag.pipeline import NOT_FOUND_TEXT
from rag.providers import ProviderError, make_embedder

from .auth import VerifiedMember, get_member, router as auth_router
from .channels import ChannelSettings, canonical_origin, refund_failed_question, reserve_question
from .chat import ChatService
from .common import api, providers, resolve_provider
from .db import Db, database_engine
from .db.models import Document
from .documents import router as documents_router
from .errors import CodedHTTPException, coded_error, provider_failure
from .observability import RequestIdMiddleware, install_log_record_factory
from .public import api as public_api, pages as public_pages
from .schemas import ChatResponse, WorkspaceChatRequest
from .settings import DocumentLimits
from .store import KeywordSearchLimit, PgVectorStore
from .usage import record_answer, router as usage_router
from .widget_assets import router as widget_assets
from .widget_sessions import RedactAccessCredentials
from .widgets import router as widgets_router

admin = APIRouter(prefix="/api/v1")
admin.include_router(auth_router)
admin.include_router(documents_router)
admin.include_router(usage_router)
admin.include_router(widgets_router)
admin.add_api_route("/providers", providers, methods=["GET"], dependencies=[Depends(get_member)])


@asynccontextmanager
async def lifespan(_app: FastAPI):
    limits = DocumentLimits.from_env()
    engine = database_engine()
    access = logging.getLogger("uvicorn.access")
    credentials = RedactAccessCredentials()
    access.addFilter(credentials)
    try:
        yield {
            "sessions": sessionmaker(engine),
            "document_limits": limits,
            "chat_service": ChatService(load_settings()),
            "channels": ChannelSettings.from_env(),
        }
    finally:
        access.removeFilter(credentials)
        engine.dispose()


@admin.post("/chat")
def chat(body: WorkspaceChatRequest, request: Request, db: Db, member: VerifiedMember) -> ChatResponse:
    selected = None if body.document_ids is None else set(body.document_ids)
    owned = select(Document.id).where(Document.workspace_id == member.workspace_id)
    if selected is not None:
        owned = owned.where(Document.id.in_(selected))
        if selected != set(db.scalars(owned)):
            raise HTTPException(404, "Document not found.")
    provider = resolve_provider(body.provider)
    reserved = reserve_question(db, request.state.channels, member.workspace_id)
    if db.scalar(owned.where(Document.status == "ready").limit(1)) is None:
        return record_answer(db, member.workspace_id, ChatResponse(status="not_found", text=NOT_FOUND_TEXT))

    # Release the authorization connection before the search opens its snapshot.
    db.rollback()
    try:
        store = PgVectorStore(request.state.sessions, make_embedder(), member.workspace_id, selected)
        history = [message.model_dump() for message in body.history]
        answer = request.state.chat_service.ask(store, body.question, history, provider, channel="ask_test")
    except KeywordSearchLimit as exc:
        raise HTTPException(422, str(exc)) from None
    except ProviderError as exc:
        refund_failed_question(db, reserved)
        raise provider_failure(exc) from None
    return record_answer(db, member.workspace_id, answer)


app = FastAPI(title="Sourced API", version="1.0.0", lifespan=lifespan)
app.include_router(api)
app.include_router(admin)
app.include_router(public_api)
app.include_router(public_pages)
app.include_router(widget_assets)


class DashboardCors:
    def __init__(self, app):
        self.app = app
        self.dashboard = None
        origin = environ.get("SOURCED_DASHBOARD_ORIGIN")
        if origin:
            self.dashboard = CORSMiddleware(
                app, allow_origins=[canonical_origin(origin)], allow_credentials=True,
                allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Content-Type", "X-CSRF-Token"],
            )

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        handler = self.app
        if (
            self.dashboard and scope["type"] == "http" and path.startswith("/api/v1/")
            and not path.startswith("/api/v1/public/") and not path.endswith("/session-tokens")
        ):
            handler = self.dashboard
        await handler(scope, receive, send)


app.add_middleware(DashboardCors)
# Added last so it wraps everything, including CORS responses.
app.add_middleware(RequestIdMiddleware)
app.add_exception_handler(CodedHTTPException, coded_error)
install_log_record_factory()


@app.exception_handler(RequestValidationError)
async def invalid_request(_request: Request, exc: RequestValidationError) -> JSONResponse:
    # Credentials and other request values should never appear in validation responses.
    detail = [{key: item[key] for key in ("loc", "msg", "type")} for item in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": detail})
