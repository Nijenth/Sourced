"""OpenAI: chat and embeddings."""
from __future__ import annotations

from .base import Provider, ProviderError, friendly_error, import_sdk, setting

DEFAULT_CHAT_MODEL = "gpt-6.1-terra"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"


class OpenAIEmbedder:
    def __init__(self, api_key: str, model: str = DEFAULT_EMBEDDING_MODEL, batch_size: int = 96):
        openai = import_sdk("openai", "openai", "OpenAI")
        self._client = openai.OpenAI(api_key=api_key, max_retries=3, timeout=60)
        self.model = model
        self.name = f"openai:{model}"
        self.batch_size = batch_size

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        try:
            for i in range(0, len(texts), self.batch_size):
                batch = [t.replace("\n", " ") or " " for t in texts[i : i + self.batch_size]]
                resp = self._client.embeddings.create(model=self.model, input=batch)
                out.extend(d.embedding for d in sorted(resp.data, key=lambda d: d.index))
        except Exception as exc:
            raise ProviderError(friendly_error("OpenAI embeddings", exc)) from exc
        return out


class OpenAIChat:
    def __init__(self, api_key: str, model: str = DEFAULT_CHAT_MODEL):
        openai = import_sdk("openai", "openai", "OpenAI")
        self._client = openai.OpenAI(api_key=api_key, max_retries=3, timeout=90)
        self.model = model
        self.name = "openai"
        self.label = f"OpenAI ({model})"

    def complete(self, system: str, messages: list[dict], max_tokens: int = 900) -> str:
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system}, *messages],
                max_completion_tokens=max_tokens,
            )
        except Exception as exc:
            raise ProviderError(friendly_error("OpenAI", exc)) from exc
        return resp.choices[0].message.content or ""


PROVIDER = Provider(
    name="openai",
    label="OpenAI",
    requires=("OPENAI_API_KEY",),
    make_chat=lambda env: OpenAIChat(env["OPENAI_API_KEY"], setting(env, "OPENAI_MODEL", default=DEFAULT_CHAT_MODEL)),
    make_embedder=lambda env, model: OpenAIEmbedder(env["OPENAI_API_KEY"], model),
    default_embedding_model=DEFAULT_EMBEDDING_MODEL,
)
