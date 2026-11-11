"""Claim and process durable Sourced ingestion jobs."""

import argparse
import logging
import math
import time
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from rag.documents import Chunk
from rag.providers import Embedder, ProviderError, make_embedder

from .db import database_engine
from .db.models import Document, DocumentChunk, DocumentFile, LoginSession, QuestionLimit
from .errors import MISCONFIGURED, TRANSIENT, provider_error_code
from .pdf_tasks import PdfTaskError, extract_chunks
from .settings import DocumentLimits, positive_int

LEASE = timedelta(minutes=15)
BATCH_SIZE = 64
MAX_ATTEMPTS = 3
RETRY_ERROR = "This document could not be indexed after three attempts. Try uploading it again."
WAITING_TO_RETRY = "The search provider is busy. Sourced will try again in a moment."
PROVIDER_DOWN_ERROR = "The search provider was unavailable, so this document couldn't be indexed. Try uploading it again later."
MISCONFIGURED_ERROR = "The search provider isn't set up correctly, so this document couldn't be indexed."
DEFAULT_RETRY_SECONDS = 30
CLEANUP_SECONDS = 600
logger = logging.getLogger(__name__)


class UnusableEmbeddings(ProviderError):
    """The provider answered, but with vectors that can't be stored; retrying won't help."""


@dataclass(frozen=True)
class Job:
    document_id: UUID
    workspace_id: UUID
    claim_id: UUID
    filename: str
    data: bytes
    attempts: int

    def document_filter(self):
        return (
            Document.id == self.document_id,
            Document.workspace_id == self.workspace_id,
            Document.claim_id == self.claim_id,
            Document.status == "processing",
        )


def claim_job(sessions: sessionmaker, embedding_model: str) -> Job | None:
    with sessions.begin() as db:
        eligible = (
            select(Document)
            .join(DocumentFile, DocumentFile.document_id == Document.id)
            .where(or_(
                # A document waiting out a retry backoff keeps lease_until as its earliest start.
                and_(Document.status == "queued", or_(Document.lease_until.is_(None), Document.lease_until <= func.now())),
                and_(Document.status == "processing", Document.lease_until <= func.now()),
                and_(Document.status == "ready", Document.embedding_model.is_distinct_from(embedding_model)),
            ))
            .order_by(Document.created_at, Document.id)
            .limit(1)
            .with_for_update(of=Document, skip_locked=True)
        )
        while True:
            document = db.scalar(eligible)
            if document is None:
                return None
            if document.status == "ready":
                document.attempts = 0
            if document.attempts < MAX_ATTEMPTS:
                break
            # The final worker may have crashed before recording a failure.
            _finish_document(db, document, embedding_model, [], [], RETRY_ERROR)
            db.flush()

        data = db.scalar(select(DocumentFile.pdf).where(DocumentFile.document_id == document.id))
        claim_id = uuid4()
        document.attempts += 1
        document.status = "processing"
        document.progress = 10
        document.error = None
        document.claim_id = claim_id
        document.lease_until = func.now() + LEASE
        return Job(document.id, document.workspace_id, claim_id, document.filename, data, document.attempts)


def update_progress(sessions: sessionmaker, job: Job, progress: int) -> bool:
    with sessions.begin() as db:
        return db.execute(
            update(Document).where(*job.document_filter()).values(progress=progress, lease_until=func.now() + LEASE)
        ).rowcount == 1


def complete_job(
    sessions: sessionmaker, job: Job, model: str,
    chunks: list[Chunk], vectors: list[list[float]], error: str | None,
) -> None:
    with sessions.begin() as db:
        document = db.scalar(select(Document).where(*job.document_filter()).with_for_update())
        if document is not None:
            _finish_document(db, document, model, chunks, vectors, error)


def _finish_document(
    db: Session, document: Document, model: str,
    chunks: list[Chunk], vectors: list[list[float]], error: str | None,
) -> None:
    db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
    if error is None:
        db.add_all(
            DocumentChunk(
                document_id=document.id, engine_chunk_id=chunk.id, page=chunk.page, ordinal=ordinal,
                text=chunk.text, embedding=vector,
            )
            for ordinal, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True))
        )
    document.status = "failed" if error else "ready"
    document.progress = 0 if error else 100
    document.embedding_model = None if error else model
    document.error = error
    document.claim_id = None
    document.lease_until = None


def requeue_job(sessions: sessionmaker, job: Job, delay_seconds: float) -> None:
    """Put a claimed document back in the queue, to be retried no earlier than `delay_seconds` from now."""
    with sessions.begin() as db:
        db.execute(update(Document).where(*job.document_filter()).values(
            status="queued", progress=0, error=WAITING_TO_RETRY, claim_id=None,
            lease_until=func.now() + timedelta(seconds=delay_seconds),
        ))


def _process_job(
    sessions: sessionmaker, job: Job, limits: DocumentLimits, embedder: Embedder, retry_seconds: float,
) -> None:
    chunks = []
    vectors = []
    error = None
    try:
        chunks = extract_chunks(job.data, job.filename, limits.characters, limits.timeout)
        for offset in range(0, len(chunks), BATCH_SIZE):
            if not update_progress(sessions, job, 50 + (49 * offset // len(chunks))):
                return
            batch = chunks[offset:offset + BATCH_SIZE]
            embedded = embedder.embed([chunk.text for chunk in batch])
            if len(embedded) != len(batch):
                raise UnusableEmbeddings("The embedding provider returned the wrong number of vectors.")
            dimensions = len(vectors[0]) if vectors else len(embedded[0])
            if not 1 <= dimensions <= 16_000 or any(
                len(vector) != dimensions or any(not math.isfinite(value) or abs(value) > 3.4028235e38 for value in vector)
                for vector in embedded
            ):
                raise UnusableEmbeddings("The embedding provider returned invalid vectors.")
            vectors.extend(embedded)
    except PdfTaskError as exc:
        error = str(exc)
    except UnusableEmbeddings:
        error = "The embedding provider returned unusable results for this document."
    except ProviderError as exc:
        code = provider_error_code(exc)
        logger.warning("Embedding failed (%s, attempt %d of %d).", code, job.attempts, MAX_ATTEMPTS)
        if code in TRANSIENT and job.attempts < MAX_ATTEMPTS:
            # Back off 1x, 2x, 4x… the base delay so a rate limit or short outage can clear.
            requeue_job(sessions, job, retry_seconds * 2 ** (job.attempts - 1))
            return
        error = MISCONFIGURED_ERROR if code == MISCONFIGURED else PROVIDER_DOWN_ERROR

    complete_job(sessions, job, embedder.name, chunks, vectors, error)


def process_one(
    sessions: sessionmaker, limits: DocumentLimits, embedder: Embedder, *, retry_seconds: float = DEFAULT_RETRY_SECONDS,
) -> bool:
    job = claim_job(sessions, embedder.name)
    if job is None:
        return False
    try:
        _process_job(sessions, job, limits, embedder, retry_seconds)
    except Exception as exc:
        if job.attempts < MAX_ATTEMPTS:
            raise
        logger.error("Ingestion reached its retry limit (%s).", type(exc).__name__)
        complete_job(sessions, job, embedder.name, [], [], RETRY_ERROR)
    return True


def cleanup(sessions: sessionmaker) -> tuple[int, int]:
    """Delete expired sign-in sessions and finished rate-limit windows."""
    with sessions.begin() as db:
        logins = db.execute(delete(LoginSession).where(LoginSession.expires_at <= func.now())).rowcount
        limits = db.execute(delete(QuestionLimit).where(QuestionLimit.expires_at <= func.now())).rowcount
    return logins, limits


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Sourced ingestion worker.")
    parser.add_argument("--once", action="store_true", help="Process at most one job and exit.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    engine = database_engine()
    try:
        sessions = sessionmaker(engine)
        limits = DocumentLimits.from_env()
        embedder = make_embedder()
        retry_seconds = positive_int("SOURCED_INGEST_RETRY_SECONDS", DEFAULT_RETRY_SECONDS)
        cleanup_seconds = positive_int("SOURCED_CLEANUP_SECONDS", CLEANUP_SECONDS)
        next_cleanup = 0.0
        while True:
            if time.monotonic() >= next_cleanup:
                try:
                    removed = cleanup(sessions)
                    logger.info("Cleanup removed %d expired sessions and %d rate-limit windows.", *removed)
                except Exception as exc:
                    logger.error("Cleanup failed (%s).", type(exc).__name__)
                next_cleanup = time.monotonic() + cleanup_seconds
            try:
                processed = process_one(sessions, limits, embedder, retry_seconds=retry_seconds)
            except Exception as exc:
                # Claims survive crashes and database interruptions until their leases expire.
                logger.error("Ingestion interrupted (%s).", type(exc).__name__)
                if args.once:
                    raise SystemExit(1) from None
                processed = False
            if args.once:
                return
            if not processed:
                time.sleep(2)
    except KeyboardInterrupt:
        return
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
