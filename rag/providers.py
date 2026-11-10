"""Embedding and chat providers: OpenAI embeddings, plus Claude or OpenAI for answers."""
from __future__ import annotations

from typing import Protocol


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class ChatModel(Protocol):
    name: str
    label: str

    def complete(self, system: str, messages: list[dict], max_tokens: int = 900) -> str: ...


class ProviderError(RuntimeError):
    """A provider call failed in a way worth showing to the user."""


class OpenAIEmbedder:
    def __init__(self, api_key: str, model: str = "text-embedding-3-small", batch_size: int = 96):
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, max_retries=3, timeout=60)
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
            raise ProviderError(_friendly("OpenAI embeddings", exc)) from exc
        return out


class ClaudeChat:
    def __init__(self, api_key: str, model: str):
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key, max_retries=3, timeout=90)
        self.model = model
        self.name = "claude"
        self.label = f"Claude ({model})"

    def complete(self, system: str, messages: list[dict], max_tokens: int = 900) -> str:
        try:
            resp = self._client.messages.create(
                model=self.model, max_tokens=max_tokens, system=system, messages=messages
            )
        except Exception as exc:
            raise ProviderError(_friendly("Claude", exc)) from exc
        return "".join(getattr(block, "text", "") for block in resp.content)


class OpenAIChat:
    def __init__(self, api_key: str, model: str):
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, max_retries=3, timeout=90)
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
            raise ProviderError(_friendly("OpenAI", exc)) from exc
        return resp.choices[0].message.content or ""


def _friendly(service: str, exc: Exception) -> str:
    text = str(exc)
    low = text.lower()
    if "401" in text or "authentication" in low or "api key" in low:
        return f"{service} rejected the API key. Check the key in your settings."
    if "429" in text or "rate limit" in low or "quota" in low:
        return f"{service} is rate-limiting requests or the account is out of credit. Try again shortly."
    if "model" in low and ("not found" in low or "does not exist" in low or "404" in text):
        return f"{service} doesn't recognise the configured model. Check the model name in your settings."
    if "timeout" in low or "timed out" in low or "connection" in low:
        return f"Couldn't reach {service}. Check your internet connection and try again."
    return f"{service} returned an error: {text[:200]}"
