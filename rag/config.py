"""App settings read from environment variables (or Streamlit secrets, copied into the env by app.py).

Provider keys and models are read by rag.providers.registry, not here.
"""
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
    top_k: int
    max_questions_per_session: int
    max_questions_per_day: int
    max_upload_files: int
    max_upload_mb: int
    max_upload_pages: int
    fake_mode: bool
    repo_url: str | None


def load_settings() -> Settings:
    return Settings(
        top_k=_int("TOP_K", 5),
        max_questions_per_session=_int("MAX_QUESTIONS_PER_SESSION", 25),
        max_questions_per_day=_int("MAX_QUESTIONS_PER_DAY", 300),
        max_upload_files=_int("MAX_UPLOAD_FILES", 3),
        max_upload_mb=_int("MAX_UPLOAD_MB", 10),
        max_upload_pages=_int("MAX_UPLOAD_PAGES", 80),
        fake_mode=os.getenv("RAG_OFFLINE_MODE", "").lower() in {"1", "true", "yes"},
        repo_url=os.getenv("REPO_URL") or None,
    )
