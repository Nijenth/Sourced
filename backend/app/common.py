"""Shared HTTP routes and provider selection for the Sourced APIs."""

from fastapi import APIRouter, HTTPException

from rag.offline import KeywordChat
from rag.providers import PROVIDERS, chat_provider_names

from .errors import ADMIN_MESSAGES, MISCONFIGURED, CodedHTTPException
from .schemas import ProviderStatus

api = APIRouter(prefix="/api/v1")


@api.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def providers() -> list[ProviderStatus]:
    ready = chat_provider_names()
    labels = {name: provider.label for name, provider in PROVIDERS.items() if provider.make_chat}
    if "offline" in ready:
        labels["offline"] = KeywordChat.label
    return [
        ProviderStatus(name=name, label=labels[name], ready=name in ready)
        for name in dict.fromkeys([*ready, *labels])
    ]


def resolve_provider(name: str | None) -> str:
    ready = chat_provider_names()
    provider = name or next(iter(ready), None)
    if provider is not None and provider not in PROVIDERS and provider != "offline":
        raise HTTPException(422, "Unknown chat provider.")
    if provider is None or provider not in ready:
        raise CodedHTTPException(503, MISCONFIGURED, "The requested chat provider is unavailable. " + ADMIN_MESSAGES[MISCONFIGURED])
    return provider
