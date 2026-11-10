"""The adapter contracts every provider implements, plus shared helpers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Protocol

Env = Mapping[str, str]


class Embedder(Protocol):
    name: str  # identifies the provider and model, e.g. "openai:text-embedding-3-small"

    def embed(self, texts: list[str]) -> list[list[float]]: ...

    # Optional: providers that embed questions differently from documents also define
    # embed_query(text) -> list[float]. VectorStore.search uses it when present.


class ChatModel(Protocol):
    name: str  # provider name, e.g. "openai"
    label: str  # shown in the model picker

    def complete(self, system: str, messages: list[dict], max_tokens: int = 900) -> str: ...


class ProviderError(RuntimeError):
    """A provider call failed in a way worth showing to the user."""


@dataclass(frozen=True)
class Provider:
    """How to build one provider's adapters from settings (environment variables)."""

    name: str
    label: str
    requires: tuple[str, ...] = ()  # settings that must be present before it can be used
    make_chat: Callable[[Env], ChatModel] | None = None
    make_embedder: Callable[[Env, str], Embedder] | None = None
    default_embedding_model: str | None = None

    def configured(self, env: Env) -> bool:
        return all(env.get(k) for k in self.requires)


def setting(env: Env, *names: str, default: str = "") -> str:
    """The first non-empty setting among `names`, else `default`."""
    for n in names:
        if env.get(n):
            return env[n]
    return default


def import_sdk(module: str, pip_name: str, service: str):
    """Import a provider SDK lazily, with a clear message if it isn't installed."""
    import importlib

    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise ProviderError(f"{service} needs the '{pip_name}' package. Run: pip install {pip_name}") from exc


def friendly_error(service: str, exc: Exception) -> str:
    text = str(exc)
    low = text.lower()
    if "401" in text or "403" in text or "authentication" in low or "api key" in low or "permission" in low:
        return f"{service} rejected the API key. Check the key in your settings."
    if "429" in text or "rate limit" in low or "quota" in low or "resource_exhausted" in low:
        if "perday" in low or "per day" in low or "daily" in low:
            return f"{service}'s daily limit for this key is used up. It resets tomorrow, or use a paid key."
        return f"{service} is rate-limiting requests or the account is out of credit. Try again shortly."
    if "503" in text or "unavailable" in low or "overloaded" in low or "500" in text:
        return f"{service} is temporarily unavailable or overloaded. Try again in a moment."
    if "model" in low and ("not found" in low or "does not exist" in low or "404" in text):
        return f"{service} doesn't recognise the configured model. Check the model name in your settings."
    if "timeout" in low or "timed out" in low or "connection" in low or "connect" in low:
        return f"Couldn't reach {service}. Check your connection and try again."
    return f"{service} returned an error. Try again, or switch provider."
