"""FastAPI entry point for the Sourced sample API."""

import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, HTTPException, Request

from rag.config import load_settings
from rag.offline import KeywordChat
from rag.pipeline import NOT_FOUND_TEXT
from rag.providers import PROVIDERS, ProviderError, chat_provider_names

from .chat import SAMPLE_DOCUMENT_ID, SampleChat
from .schemas import ChatRequest, ChatResponse, ProviderStatus

logger = logging.getLogger(__name__)
api = APIRouter(prefix="/api/v1")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    sample_chat = SampleChat(load_settings())
    try:
        yield {"sample_chat": sample_chat}
    finally:
        sample_chat.close()


@api.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@api.get("/providers")
def providers() -> list[ProviderStatus]:
    ready = chat_provider_names()
    labels = {name: provider.label for name, provider in PROVIDERS.items() if provider.make_chat}
    if "offline" in ready:
        labels["offline"] = KeywordChat.label
    return [
        ProviderStatus(name=name, label=labels[name], ready=name in ready)
        for name in dict.fromkeys([*ready, *labels])
    ]


@api.post("/chat")
def chat(body: ChatRequest, request: Request) -> ChatResponse:
    if body.document_ids is not None:
        if not body.document_ids:
            return ChatResponse(status="not_found", text=NOT_FOUND_TEXT)
        if body.document_ids != [SAMPLE_DOCUMENT_ID]:
            raise HTTPException(status_code=404, detail="Document not found.")

    ready = chat_provider_names()
    provider = body.provider or next(iter(ready), None)
    if provider is not None and provider not in PROVIDERS and provider != "offline":
        raise HTTPException(status_code=422, detail="Unknown chat provider.")
    if provider not in ready:
        raise HTTPException(status_code=503, detail="The requested chat provider is unavailable.")

    sample_chat: SampleChat = request.state.sample_chat
    try:
        return sample_chat.ask(body.question, [message.model_dump() for message in body.history], provider)
    except ProviderError:
        logger.warning("Sample chat could not complete a provider operation.")
        raise HTTPException(
            status_code=503,
            detail="A provider could not complete the request. Try again or check the configuration.",
        ) from None


app = FastAPI(title="Sourced API", version="1.0.0", lifespan=lifespan)
app.include_router(api)
