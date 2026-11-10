"""Settings read from environment variables (or Streamlit secrets, copied into the env by app.py)."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    openai_api_key: str | None
    anthropic_api_key: str | None
    claude_model: str
    openai_model: str
    embedding_model: str
    top_k: int
    max_questions_per_session: int
    max_questions_per_day: int
    max_upload_files: int
    max_upload_mb: int
    max_upload_pages: int
    fake_mode: bool
    repo_url: str | None

    @property
    def providers(self) -> list[str]:
        """Answer providers that have a key configured, in display order."""
        if self.fake_mode:
            return ["offline"]
        out = []
        if self.anthropic_api_key:
            out.append("claude")
        if self.openai_api_key:
            out.append("openai")
        return out

    @property
    def can_embed(self) -> bool:
        return self.fake_mode or bool(self.openai_api_key)


def load_settings() -> Settings:
    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        claude_model=os.getenv("CLAUDE_MODEL", "claude-haiku-5-5"),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-6.1-terra"),
        embedding_model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
        top_k=_int("TOP_K", 5),
        max_questions_per_session=_int("MAX_QUESTIONS_PER_SESSION", 25),
        max_questions_per_day=_int("MAX_QUESTIONS_PER_DAY", 300),
        max_upload_files=_int("MAX_UPLOAD_FILES", 3),
        max_upload_mb=_int("MAX_UPLOAD_MB", 10),
        max_upload_pages=_int("MAX_UPLOAD_PAGES", 80),
        fake_mode=os.getenv("RAG_OFFLINE_MODE", "").lower() in {"1", "true", "yes"},
        repo_url=os.getenv("REPO_URL") or None,
    )
