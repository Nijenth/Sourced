"""Anthropic (Claude): chat only. Anthropic has no embeddings API."""
from __future__ import annotations

from .base import Provider, ProviderError, friendly_error, import_sdk, setting

DEFAULT_CHAT_MODEL = "claude-haiku-5-5"


class ClaudeChat:
    def __init__(self, api_key: str, model: str = DEFAULT_CHAT_MODEL):
        anthropic = import_sdk("anthropic", "anthropic", "Anthropic")
        self._client = anthropic.Anthropic(api_key=api_key, max_retries=3, timeout=90)
        self.model = model
        self.name = "anthropic"
        self.label = f"Claude ({model})"

    def complete(self, system: str, messages: list[dict], max_tokens: int = 900) -> str:
        try:
            resp = self._client.messages.create(model=self.model, max_tokens=max_tokens, system=system, messages=messages)
        except Exception as exc:
            raise ProviderError(friendly_error("Claude", exc)) from exc
        return "".join(getattr(block, "text", "") for block in resp.content)


PROVIDER = Provider(
    name="anthropic",
    label="Anthropic",
    requires=("ANTHROPIC_API_KEY",),
    make_chat=lambda env: ClaudeChat(
        env["ANTHROPIC_API_KEY"], setting(env, "ANTHROPIC_MODEL", "CLAUDE_MODEL", default=DEFAULT_CHAT_MODEL)
    ),
)
