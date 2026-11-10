"""Google Gemini (Gemini Developer API via the google-genai SDK): chat and embeddings.

Notes from Google's docs (checked November 2026; gemini-3.5-flash is deprecated and routed to 3.6):
- Gemini 3 models "think" before answering, and thinking tokens count against max_output_tokens,
  so the adapter sets a low thinking level and adds headroom on top of the answer budget.
- gemini-embedding-2 has no task_type setting; it expects text prefixes instead, different for
  questions and documents. gemini-embedding-001 uses task_type.
- Free-tier requests may be used by Google to improve its products; paid tier is not.
"""
from __future__ import annotations

from .base import Provider, ProviderError, friendly_error, import_sdk, setting

DEFAULT_CHAT_MODEL = "gemini-3.6-flash"
DEFAULT_EMBEDDING_MODEL = "gemini-embedding-2"
DEFAULT_DIMENSIONS = 768
DEFAULT_THINKING_LEVEL = "low"
THINKING_HEADROOM = 2048  # extra output tokens reserved for thinking


def _sdk():
    genai = import_sdk("google.genai", "google-genai", "Gemini")
    types = import_sdk("google.genai.types", "google-genai", "Gemini")
    return genai, types


def _uses_thinking_level(model: str) -> bool:
    # Gemini 3+ (and aliases like gemini-flash-latest) take thinking_level; 2.5 models don't.
    return model.startswith("gemini-") and not model.startswith("gemini-2")


class GeminiChat:
    def __init__(self, api_key: str, model: str = DEFAULT_CHAT_MODEL, thinking_level: str = DEFAULT_THINKING_LEVEL, client=None):
        genai, self._types = _sdk()
        self._client = client or genai.Client(api_key=api_key, http_options=self._types.HttpOptions(timeout=90_000))
        self.model = model
        self.thinking_level = thinking_level if thinking_level not in ("", "off", "none") else ""
        self.name = "gemini"
        self.label = f"Google Gemini ({model})"

    def complete(self, system: str, messages: list[dict], max_tokens: int = 900) -> str:
        t = self._types
        contents = [
            t.Content(role="model" if m["role"] == "assistant" else "user", parts=[t.Part.from_text(text=m["content"])])
            for m in messages
        ]
        config = {"system_instruction": system, "max_output_tokens": max_tokens}
        if self.thinking_level and _uses_thinking_level(self.model):
            config["thinking_config"] = t.ThinkingConfig(thinking_level=self.thinking_level)
            config["max_output_tokens"] = max_tokens + THINKING_HEADROOM
        try:
            resp = self._client.models.generate_content(
                model=self.model, contents=contents, config=t.GenerateContentConfig(**config)
            )
            return resp.text or ""
        except Exception as exc:
            raise ProviderError(friendly_error("Gemini", exc)) from exc


class GeminiEmbedder:
    def __init__(self, api_key: str, model: str = DEFAULT_EMBEDDING_MODEL, dimensions: int = DEFAULT_DIMENSIONS,
                 batch_size: int = 50, client=None):
        genai, self._types = _sdk()
        self._client = client or genai.Client(api_key=api_key, http_options=self._types.HttpOptions(timeout=60_000))
        self.model = model
        self.dimensions = dimensions
        self.batch_size = batch_size
        self.name = f"gemini:{model}:{dimensions}"

    @property
    def _v2(self) -> bool:
        return self.model.startswith("gemini-embedding-2")

    def _call(self, texts: list[str], task_type: str) -> list[list[float]]:
        t = self._types
        if self._v2:
            # One Content per text returns one embedding per text (plain strings would be merged into one).
            contents = [t.Content(parts=[t.Part.from_text(text=x)]) for x in texts]
            config = t.EmbedContentConfig(output_dimensionality=self.dimensions)
        else:
            contents = list(texts)
            config = t.EmbedContentConfig(output_dimensionality=self.dimensions, task_type=task_type)
        try:
            resp = self._client.models.embed_content(model=self.model, contents=contents, config=config)
            vectors = [list(e.values) for e in resp.embeddings]
        except Exception as exc:
            raise ProviderError(friendly_error("Gemini embeddings", exc)) from exc
        if len(vectors) != len(texts):
            raise ProviderError("Gemini embeddings returned an unexpected number of results. Try again.")
        return vectors

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed document chunks."""
        prepared = [f"title: none | text: {x}" if self._v2 else x for x in texts]
        out: list[list[float]] = []
        for i in range(0, len(prepared), self.batch_size):
            out.extend(self._call(prepared[i : i + self.batch_size], "RETRIEVAL_DOCUMENT"))
        return out

    def embed_query(self, text: str) -> list[float]:
        """Embed a search question (formatted differently from documents)."""
        q = f"task: question answering | query: {text}" if self._v2 else text
        return self._call([q], "QUESTION_ANSWERING")[0]


def _int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


PROVIDER = Provider(
    name="gemini",
    label="Google Gemini",
    requires=("GEMINI_API_KEY",),
    make_chat=lambda env: GeminiChat(
        env["GEMINI_API_KEY"],
        setting(env, "GEMINI_MODEL", default=DEFAULT_CHAT_MODEL),
        setting(env, "GEMINI_THINKING_LEVEL", default=DEFAULT_THINKING_LEVEL).lower(),
    ),
    make_embedder=lambda env, model: GeminiEmbedder(
        env["GEMINI_API_KEY"], model, _int(env.get("GEMINI_EMBEDDING_DIMENSIONS", ""), DEFAULT_DIMENSIONS)
    ),
    default_embedding_model=DEFAULT_EMBEDDING_MODEL,
)
