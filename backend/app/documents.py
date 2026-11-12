"""Authenticated upload, document status, deletion, and page previews."""

import asyncio
import json
from datetime import datetime
from hashlib import sha256
from os import environ
from typing import Annotated, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from rag.providers import PROVIDERS, embedding_provider_name

from .auth import CurrentMember, VerifiedMember, get_member
from .db import Db
from .db.models import Document, DocumentFile, PagePreview
from .errors import CodedHTTPException
from .pdf_tasks import PdfBusy, PdfTaskError, run_pdf_task
from .settings import UploadQuota, positive_int

router = APIRouter(prefix="/documents", tags=["documents"])
MULTIPART_OVERHEAD = 64 * 1024
PENDING = ("queued", "processing")
UPLOAD_ADMISSION_LOCK = 0x50D0_0001  # Postgres advisory lock: one upload admission decision at a time


class DocumentStatus(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    filename: str
    pages: int
    status: Literal["queued", "processing", "ready", "failed"]
    progress: int
    error: str | None
    created_at: datetime


class QueuedDocument(BaseModel):
    id: UUID
    status: Literal["queued"] = "queued"


class UploadSettings(BaseModel):
    max_bytes: int
    max_pages: int
    embedding_provider: str | None


class HighlightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quote: str = Field(min_length=1, max_length=2000)


class UploadTooLarge(MultiPartException):
    pass


def enqueue_document(db: Db, workspace_id: UUID, filename: str, data: bytes, pages: int) -> UUID:
    document = Document(
        workspace_id=workspace_id, filename=filename, sha256=sha256(data).hexdigest(),
        pages=pages, status="queued", progress=0,
    )
    db.add(document)
    db.flush()
    db.add(DocumentFile(document_id=document.id, pdf=data))
    return document.id


async def read_upload(request: Request, db: Db, _member: VerifiedMember) -> tuple[str, bytes]:
    max_bytes = request.state.document_limits.bytes
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "multipart/form-data":
        raise HTTPException(415, "Upload the PDF as multipart/form-data.")
    # Hand the database connection back while the file arrives: a slow upload must not hold one (audit N01).
    # The upload route checks the sign-in again before saving anything.
    db.rollback()
    try:
        async with asyncio.timeout(positive_int("SOURCED_UPLOAD_READ_SECONDS", 60)):
            return await _read_pdf_form(request, max_bytes)
    except TimeoutError:
        raise HTTPException(408, "The upload took too long to arrive. Try again.", {"Connection": "close"}) from None


async def _read_pdf_form(request: Request, max_bytes: int) -> tuple[str, bytes]:
    async def limited_stream():
        size = 0
        async for block in request.stream():
            size += len(block)
            if size > max_bytes + MULTIPART_OVERHEAD:
                raise UploadTooLarge("The upload is too large.")
            yield block

    try:
        form = await MultiPartParser(request.headers, limited_stream(), max_files=1, max_fields=0).parse()
    except UploadTooLarge:
        raise HTTPException(413, "The upload is too large.") from None
    except (MultiPartException, ValueError):
        raise HTTPException(422, "Upload one PDF in the file field.") from None

    try:
        upload = form.get("file")
        if not isinstance(upload, UploadFile) or not upload.filename:
            raise HTTPException(422, "Upload one PDF in the file field.")
        filename = upload.filename.replace("\\", "/").rsplit("/", 1)[-1]
        if not filename.lower().endswith(".pdf") or len(filename) > 255 or not filename.isprintable():
            raise HTTPException(422, "The upload needs a valid PDF filename.")
        data = await upload.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise HTTPException(413, "The upload is too large.")
        if b"%PDF-" not in data[:1024]:
            raise HTTPException(422, "This file is not a PDF.")
        return filename, data
    finally:
        await form.close()


@router.post(
    "", status_code=202,
    openapi_extra={"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
        "type": "object", "required": ["file"], "properties": {"file": {"type": "string", "format": "binary"}},
    }}}}},
)
def upload_document(
    request: Request, response: Response, db: Db, member: VerifiedMember,
    upload: Annotated[tuple[str, bytes], Depends(read_upload)],
) -> QueuedDocument:
    filename, data = upload
    limits = request.state.document_limits
    quota = UploadQuota.from_env()
    admit_upload(db, member.workspace_id, len(data), quota)  # fail fast, before spending a PDF slot
    db.rollback()  # no connection held while waiting for or running the PDF reader (audit N01)
    try:
        pages = int(run_pdf_task("count", data, timeout=limits.timeout))
    except PdfTaskError as exc:
        raise pdf_failure(exc, str(exc)) from None
    if not 1 <= pages <= limits.pages:
        raise HTTPException(422, f"Upload a PDF with 1 to {limits.pages} pages.")
    # The final decision runs in a fresh READ COMMITTED transaction under an advisory lock, so it sees every
    # upload committed before it and simultaneous uploads can't all squeeze past the limits (audit N02).
    db.connection(execution_options={"isolation_level": "READ COMMITTED"})
    if get_member(request, response, db).workspace_id != member.workspace_id:  # still signed in, still admin
        raise HTTPException(401, "Sign in to Sourced.")
    admit_upload(db, member.workspace_id, len(data), quota, lock=True)
    result = QueuedDocument(id=enqueue_document(db, member.workspace_id, filename, data, pages))
    db.commit()
    return result


def admit_upload(db: Db, workspace_id: UUID, size: int, quota: UploadQuota, lock: bool = False) -> None:
    """Refuse an upload that would take the workspace or the server past its limits (security audit S02).

    This governs uploads from workspace admins. The demo seed and re-indexing after an embedding-model change
    are operator actions and don't pass through it. Storage counts the stored PDFs, not derived text,
    vectors or page pictures."""
    if lock:
        db.execute(select(func.pg_advisory_xact_lock(UPLOAD_ADMISSION_LOCK)))
    documents, stored, pending = db.execute(
        select(
            func.count(Document.id),
            func.coalesce(func.sum(func.octet_length(DocumentFile.pdf)), 0),
            func.count(Document.id).filter(Document.status.in_(PENDING)),
        )
        .select_from(Document).outerjoin(DocumentFile, DocumentFile.document_id == Document.id)
        .where(Document.workspace_id == workspace_id)
    ).one()
    if documents >= quota.documents:
        raise CodedHTTPException(409, "workspace_full", (
            f"This workspace has reached its limit of {quota.documents} documents. Delete one to upload another."))
    if stored + size > quota.storage_bytes:
        raise CodedHTTPException(409, "workspace_full", (
            f"This upload would take the workspace past its {quota.storage_bytes // (1024 * 1024)} MB storage limit. "
            "Delete a document to make room."))
    if pending >= quota.pending:
        raise CodedHTTPException(429, "uploads_pending", (
            f"{pending} uploads are still being processed. Wait for them to finish, then upload again."),
            {"Retry-After": "30"})
    if db.scalar(select(func.count()).select_from(Document).where(Document.status.in_(PENDING))) >= quota.pending_total:
        raise CodedHTTPException(503, "server_busy", "The server is processing many uploads. Try again in a few minutes.",
                                 {"Retry-After": "60"})


def pdf_failure(exc: PdfTaskError, message: str) -> HTTPException:
    if isinstance(exc, PdfBusy):
        return CodedHTTPException(503, "pdf_busy", str(exc), {"Retry-After": "10"})
    return HTTPException(422, message)


@router.get("")
def list_documents(db: Db, member: CurrentMember) -> list[DocumentStatus]:
    documents = db.scalars(
        select(Document).where(Document.workspace_id == member.workspace_id)
        .order_by(Document.created_at.desc(), Document.id)
    )
    return [DocumentStatus.model_validate(document) for document in documents]


@router.get("/settings")
def upload_settings(request: Request, _member: CurrentMember) -> UploadSettings:
    limits = request.state.document_limits
    name = embedding_provider_name()
    label = "Offline keyword matching" if name == "offline" else None
    if name in PROVIDERS and PROVIDERS[name].make_embedder and PROVIDERS[name].configured(environ):
        label = PROVIDERS[name].label
    return UploadSettings(max_bytes=limits.bytes, max_pages=limits.pages, embedding_provider=label)


@router.get("/{document_id}")
def get_document(document_id: UUID, db: Db, member: CurrentMember) -> DocumentStatus:
    document = db.scalar(select(Document).where(Document.id == document_id, Document.workspace_id == member.workspace_id))
    if document is None:
        raise HTTPException(404, "Document not found.")
    return DocumentStatus.model_validate(document)


@router.delete("/{document_id}", status_code=204)
def delete_document(document_id: UUID, db: Db, member: VerifiedMember) -> Response:
    db.execute(delete(Document).where(Document.id == document_id, Document.workspace_id == member.workspace_id))
    db.commit()
    return Response(status_code=204)


def owned_page(db: Db, workspace_id: UUID, document_id: UUID, page: int) -> UUID:
    owned_id = db.scalar(
        select(Document.id)
        .where(
            Document.id == document_id, Document.workspace_id == workspace_id,
            Document.status == "ready", Document.pages >= page, page >= 1,
        )
        .with_for_update(read=True)
    )
    if owned_id is None:
        raise HTTPException(404, "Document page not found.")
    return owned_id


@router.get("/{document_id}/pdf")
def full_pdf(document_id: UUID, db: Db, member: CurrentMember) -> Response:
    row = db.execute(
        select(Document.filename, DocumentFile.pdf).join(DocumentFile, DocumentFile.document_id == Document.id)
        .where(Document.id == document_id, Document.workspace_id == member.workspace_id, Document.status == "ready")
    ).one_or_none()
    if row is None:
        raise HTTPException(404, "Document not found.")
    return Response(row.pdf, media_type="application/pdf", headers={
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(row.filename, safe='')}",
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Cross-Origin-Resource-Policy": "same-origin",
    })


@router.post("/{document_id}/pages/{page}/highlight")
def highlighted_preview(
    document_id: UUID, page: int, body: HighlightRequest, request: Request, response: Response, db: Db,
    member: VerifiedMember,
) -> Response:
    owned_id = owned_page(db, member.workspace_id, document_id, page)
    data = db.scalar(select(DocumentFile.pdf).where(DocumentFile.document_id == owned_id))
    if data is None:
        raise HTTPException(404, "Document page not found.")
    db.rollback()  # no connection or lock held while waiting for or running the PDF reader (audit N01)
    try:
        payload = json.dumps(body.quote).encode() + b"\n" + data
        png = run_pdf_task("highlight", payload, str(page), timeout=request.state.document_limits.timeout)
    except PdfTaskError as exc:
        raise pdf_failure(exc, "This page could not be rendered.") from None
    # Check again: the sign-in or the document may have gone while the page was drawn.
    get_member(request, response, db)
    owned_page(db, member.workspace_id, document_id, page)
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, no-store"})


@router.get("/{document_id}/pages/{page}.png", responses={200: {"content": {"image/png": {}}}})
def preview(document_id: UUID, page: int, request: Request, response: Response, db: Db, member: CurrentMember) -> Response:
    png = page_png(db, member.workspace_id, document_id, page, request.state.document_limits.timeout)
    get_member(request, response, db)  # still signed in after a page that had to be drawn
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, no-store"})


def page_png(db: Db, workspace_id: UUID, document_id: UUID, page: int, timeout: int) -> bytes:
    """The page as a PNG, drawn once and cached. While a page is drawn no database connection or lock is held
    (audit N01), so callers must check their own authorization again afterwards."""
    owned_id = owned_page(db, workspace_id, document_id, page)
    png = db.scalar(select(PagePreview.png).where(PagePreview.document_id == owned_id, PagePreview.page == page))
    if png is None:
        data = db.scalar(select(DocumentFile.pdf).where(DocumentFile.document_id == owned_id))
        if data is None:
            raise HTTPException(404, "Document page not found.")
        db.rollback()
        try:
            png = run_pdf_task("preview", data, str(page), timeout=timeout)
        except PdfTaskError as exc:
            raise pdf_failure(exc, "This page could not be rendered.") from None
        owned_id = owned_page(db, workspace_id, document_id, page)  # still there and ready before caching
        db.execute(insert(PagePreview).values(document_id=owned_id, page=page, png=png).on_conflict_do_nothing())
        db.commit()
    return png
