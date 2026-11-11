"""Ollama: chat and embeddings with models running on your own machine. No key, no rate limits.

Talks to the Ollama HTTP API with the standard library (no extra package):
- POST /api/chat  with stream=false, think=false by default (thinking models like qwen3 are slow
  locally and can spend the whole token budget thinking), options.num_predict / num_ctx.
- POST /api/embed with a list of inputs; returns {"embeddings": [[...], ...]}.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Callable

from .base import Provider, ProviderError, setting

DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_EMBEDDING_MODEL = "nomic-embed-text"
DEFAULT_NUM_CTX = 8192
DEFAULT_TIMEOUT = 300  # seconds; a large local model can take a while to load the first time

# Some embedding models expect different prefixes for documents and questions (from their model cards).
_PREFIXES: list[tuple[str, str, str]] = [
    # (model name starts with, document prefix, question prefix)
    ("nomic-embed-text", "search_document: ", "search_query: "),
    ("embeddinggemma", "title: none | text: ", "task: search result | query: "),
    ("qwen3-embedding", "", "Instruct: Given a question, retrieve passages that answer the question\nQuery: "),
]
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)

Transport = Callable[[str, dict], dict]


class _HTTPError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"{status}: {body}")
        self.status, self.body = status, body


def normalise_host(host: str) -> str:
    """Accept Ollama's own OLLAMA_HOST formats ("0.0.0.0", "127.0.0.1:11434", full URLs)."""
    host = (host or DEFAULT_HOST).strip().rstrip("/")
    if "://" not in host:
        host = "http://" + host
    host = host.replace("://0.0.0.0", "://127.0.0.1")
    if host.count(":") == 1:  # no port given
        host += ":11434"
    return host


def http_transport(host: str, timeout: float) -> Transport:
    def post(path: str, payload: dict) -> dict:
        req = urllib.request.Request(
            host + path, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            raise _HTTPError(exc.code, exc.read().decode(errors="replace")) from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            if "timed out" in str(exc).lower():
                raise ProviderError(
                    "Ollama took too long to answer. The model may still be loading; try again, or raise OLLAMA_TIMEOUT."
                ) from exc
            raise ProviderError(f"Couldn't reach Ollama at {host}. Make sure the Ollama app is running.") from exc

    return post


def _friendly(exc: _HTTPError, model: str) -> str:
    low = exc.body.lower()
    if exc.status == 404 or "not found" in low:
        return f"Ollama doesn't have the model '{model}'. Download it with: ollama pull {model}"
    if "does not support" in low and "embed" in low:
        return f"'{model}' can't create embeddings. Use an embedding model such as {DEFAULT_EMBEDDING_MODEL}."
    if "memory" in low:
        return f"Ollama ran out of memory loading '{model}'. Try a smaller model."
    return "Ollama returned an error. Check that the model name is right and try again."


class OllamaChat:
    def __init__(self, model: str, host: str = DEFAULT_HOST, think: str = "false", num_ctx: int = DEFAULT_NUM_CTX,
                 timeout: float = DEFAULT_TIMEOUT, transport: Transport | None = None):
        self.host = normalise_host(host)
        self._post = transport or http_transport(self.host, timeout)
        self.model = model
        self.num_ctx = num_ctx
        self._think = {"false": False, "off": False, "true": True, "on": True}.get(think.lower(), think.lower())
        if think.lower() in ("", "auto", "default"):
            self._think = None  # let the model decide
        self.name = "ollama"
        short = re.sub(r"[-_](q\d\w*|fp16|fp32|bf16)$", "", model, flags=re.IGNORECASE)  # qwen3.6:35b-a3b-q4_K_M
        self.label = f"Ollama ({short}, local)"

    def complete(self, system: str, messages: list[dict], max_tokens: int = 900) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "stream": False,
            "options": {"num_predict": max_tokens, "num_ctx": self.num_ctx},
        }
        if self._think is not None:
            payload["think"] = self._think
        try:
            data = self._post("/api/chat", payload)
        except _HTTPError as exc:
            if "think" in payload and "think" in exc.body.lower():
                # This model doesn't support the think switch: remember that and retry without it.
                self._think = None
                return self.complete(system, messages, max_tokens)
            raise ProviderError(_friendly(exc, self.model)) from exc
        content = (data.get("message") or {}).get("content") or ""
        return _THINK_BLOCK.sub("", content).strip()


class OllamaEmbedder:
    def __init__(self, model: str = DEFAULT_EMBEDDING_MODEL, host: str = DEFAULT_HOST, batch_size: int = 32,
                 timeout: float = DEFAULT_TIMEOUT, transport: Transport | None = None):
        self.host = normalise_host(host)
        self._post = transport or http_transport(self.host, timeout)
        self.model = model
        self.batch_size = batch_size
        self.name = f"ollama:{model}"
        self._doc_prefix, self._query_prefix = next(
            ((d, q) for start, d, q in _PREFIXES if model.startswith(start)), ("", "")
        )

    def _call(self, texts: list[str]) -> list[list[float]]:
        try:
            data = self._post("/api/embed", {"model": self.model, "input": texts})
        except _HTTPError as exc:
            raise ProviderError(_friendly(exc, self.model)) from exc
        vectors = data.get("embeddings") or []
        if len(vectors) != len(texts):
            raise ProviderError("Ollama returned an unexpected number of embeddings. Try again.")
        return vectors

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed document chunks."""
        prepared = [self._doc_prefix + t for t in texts]
        out: list[list[float]] = []
        for i in range(0, len(prepared), self.batch_size):
            out.extend(self._call(prepared[i : i + self.batch_size]))
        return out

    def embed_query(self, text: str) -> list[float]:
        """Embed a search question."""
        return self._call([self._query_prefix + text])[0]


def _num(value: str, default, cast=int):
    try:
        return cast(value)
    except (TypeError, ValueError):
        return default


PROVIDER = Provider(
    name="ollama",
    label="Ollama (local)",
    requires=("OLLAMA_MODEL",),  # the chat model to use, e.g. qwen3.6:35b-a3b-q4_K_M
    make_chat=lambda env: OllamaChat(
        env["OLLAMA_MODEL"],
        host=setting(env, "OLLAMA_HOST", default=DEFAULT_HOST),
        think=setting(env, "OLLAMA_THINK", default="false"),
        num_ctx=_num(env.get("OLLAMA_NUM_CTX"), DEFAULT_NUM_CTX),
        timeout=_num(env.get("OLLAMA_TIMEOUT"), DEFAULT_TIMEOUT, float),
    ),
    make_embedder=lambda env, model: OllamaEmbedder(
        model,
        host=setting(env, "OLLAMA_HOST", default=DEFAULT_HOST),
        timeout=_num(env.get("OLLAMA_TIMEOUT"), DEFAULT_TIMEOUT, float),
    ),
    default_embedding_model=DEFAULT_EMBEDDING_MODEL,
)
