"""GroqCloud: very fast chat on open-weight models. Chat only (Groq has no embeddings API).

Groq's API is OpenAI-compatible, so this reuses OpenAIChat with Groq's endpoint.
Notes from Groq's docs (checked November 2026):
- Production models open to developer accounts: openai/gpt-oss-120b and openai/gpt-oss-20b.
- gpt-oss models reason before answering and that counts against max_completion_tokens,
  so reasoning_effort defaults to "low" with extra token headroom.
"""
from __future__ import annotations

import re

from .base import Provider, setting
from .openai_provider import OpenAIChat

BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_CHAT_MODEL = "openai/gpt-oss-120b"
DEFAULT_REASONING = "low"
REASONING_HEADROOM = 2048
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)


class GroqChat(OpenAIChat):
    service = "Groq"

    def __init__(self, api_key: str, model: str = DEFAULT_CHAT_MODEL, reasoning_effort: str = DEFAULT_REASONING,
                 client=None):
        super().__init__(api_key, model, base_url=BASE_URL, client=client)
        self.name = "groq"
        self.label = f"GroqCloud ({model})"
        effort = (reasoning_effort or "").lower()
        if model.startswith("openai/gpt-oss") and effort in ("low", "medium", "high"):
            self.extra_body = {"reasoning_effort": effort}
            self.token_headroom = REASONING_HEADROOM
        elif model.startswith("qwen/") and effort in ("off", "none", "low"):
            self.extra_body = {"reasoning_effort": "none"}  # Qwen on Groq: thinking off

    def complete(self, system: str, messages: list[dict], max_tokens: int = 900) -> str:
        return _THINK_BLOCK.sub("", super().complete(system, messages, max_tokens)).strip()


PROVIDER = Provider(
    name="groq",
    label="GroqCloud",
    requires=("GROQ_API_KEY",),
    make_chat=lambda env: GroqChat(
        env["GROQ_API_KEY"],
        setting(env, "GROQ_MODEL", default=DEFAULT_CHAT_MODEL),
        setting(env, "GROQ_REASONING_EFFORT", default=DEFAULT_REASONING),
    ),
)
