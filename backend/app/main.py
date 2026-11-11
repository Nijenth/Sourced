"""FastAPI entry point for the authenticated Sourced API."""

from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from rag.config import load_settings
from rag.pipeline import NOT_FOUND_TEXT
from rag.providers import ProviderError, make_embedder

from .auth import VerifiedMember, get_member, router as auth_router
from .chat import ChatService
from .common import api, provider_failure, providers, resolve_provider
from .db import Db, database_engine
from .db.models import Document
from .documents import router as documents_router
from .schemas import ChatResponse, WorkspaceChatRequest
from .settings import DocumentLimits
from .store import KeywordSearchLimit, PgVectorStore

admin = APIRouter(prefix="/api/v1")
admin.include_router(auth_router)
admin.include_router(documents_router)
admin.add_api_route("/providers", providers, methods=["GET"], dependencies=[Depends(get_member)])


@asynccontextmanager
async def lifespan(_app: FastAPI):
    limits = DocumentLimits.from_env()
    engine = database_engine()
    try:
        yield {
            "sessions": sessionmaker(engine),
            "document_limits": limits,
            "chat_service": ChatService(load_settings()),
        }
    finally:
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
    if db.scalar(owned.where(Document.status == "ready").limit(1)) is None:
        return ChatResponse(status="not_found", text=NOT_FOUND_TEXT)

    # Release the authorization connection before the search opens its snapshot.
    db.rollback()
    try:
        store = PgVectorStore(request.state.sessions, make_embedder(), member.workspace_id, selected)
        history = [message.model_dump() for message in body.history]
        return request.state.chat_service.ask(store, body.question, history, provider)
    except KeywordSearchLimit as exc:
        raise HTTPException(422, str(exc)) from None
    except ProviderError:
        raise provider_failure() from None


app = FastAPI(title="Sourced API", version="1.0.0", lifespan=lifespan)
app.include_router(api)
app.include_router(admin)


@app.exception_handler(RequestValidationError)
async def invalid_request(_request: Request, exc: RequestValidationError) -> JSONResponse:
    # Credentials and other request values should never appear in validation responses.
    detail = [{key: item[key] for key in ("loc", "msg", "type")} for item in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": detail})
