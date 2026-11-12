"""Limits shared by the Sourced API and ingestion worker."""

import os
from dataclasses import dataclass


def positive_int(name: str, default: int) -> int:
    value = int(os.environ.get(name, default))
    if value < 1:
        raise ValueError(f"{name} must be a positive integer.")
    return value


@dataclass(frozen=True)
class DocumentLimits:
    bytes: int
    pages: int
    characters: int
    timeout: int

    @classmethod
    def from_env(cls):
        return cls(
            bytes=positive_int("MAX_UPLOAD_MB", 10) * 1024 * 1024,
            pages=positive_int("MAX_UPLOAD_PAGES", 80),
            characters=positive_int("SOURCED_MAX_PDF_CHARS", 500_000),
            timeout=positive_int("SOURCED_PDF_TIMEOUT_SECONDS", 20),
        )


@dataclass(frozen=True)
class UploadQuota:
    """How much one workspace may store and queue, and how many uploads the server processes at once
    (security audit S02). Uploads past these limits are refused before they are read or saved."""

    documents: int
    storage_bytes: int
    pending: int
    pending_total: int

    @classmethod
    def from_env(cls):
        return cls(
            documents=positive_int("SOURCED_WORKSPACE_MAX_DOCUMENTS", 100),
            storage_bytes=positive_int("SOURCED_WORKSPACE_MAX_STORAGE_MB", 250) * 1024 * 1024,
            pending=positive_int("SOURCED_WORKSPACE_MAX_PENDING", 5),
            pending_total=positive_int("SOURCED_MAX_PENDING_DOCUMENTS", 25),
        )
