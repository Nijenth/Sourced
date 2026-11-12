"""The standalone, fictional Sourced handbook API from M1."""

from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, HTTPException, Request

from rag.config import load_settings
from rag.pipeline import NOT_FOUND_TEXT
from rag.providers import ProviderError

from .chat import SAMPLE_DOCUMENT_ID, SampleChat
from .common import api, providers, resolve_provider
from .errors import CodedHTTPException, coded_error, provider_failure
from .limits import BodySizeLimit
from .observability import RequestIdMiddleware, install_log_record_factory
from .schemas import ChatRequest, ChatResponse

sample = APIRouter(prefix="/api/v1")
sample.add_api_route("/providers", providers, methods=["GET"])


@asynccontextmanager
async def lifespan(_app: FastAPI):
    sample_chat = SampleChat(load_settings())
    try:
        yield {"sample_chat": sample_chat}
    finally:
        sample_chat.close()


@sample.post("/chat")
def chat(body: ChatRequest, request: Request) -> ChatResponse:
    if body.document_ids is not None:
        if not body.document_ids:
            return ChatResponse(status="not_found", text=NOT_FOUND_TEXT)
        if body.document_ids != [SAMPLE_DOCUMENT_ID]:
            raise HTTPException(status_code=404, detail="Document not found.")

    provider = resolve_provider(body.provider)
    sample_chat: SampleChat = request.state.sample_chat
    try:
        return sample_chat.ask(body.question, [message.model_dump() for message in body.history], provider)
    except ProviderError as exc:
        raise provider_failure(exc) from None


app = FastAPI(title="Sourced Sample API", version="1.0.0", lifespan=lifespan)
app.include_router(api)
app.include_router(sample)
app.add_exception_handler(CodedHTTPException, coded_error)
app.add_middleware(BodySizeLimit)
app.add_middleware(RequestIdMiddleware)
install_log_record_factory()
