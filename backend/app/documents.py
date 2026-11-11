"""Authenticated upload, document status, deletion, and page previews."""

from datetime import datetime
from hashlib import sha256
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from .auth import CurrentMember, VerifiedMember
from .db import Db
from .db.models import Document, DocumentFile, PagePreview
from .pdf_tasks import PdfTaskError, run_pdf_task

router = APIRouter(prefix="/documents", tags=["documents"])
MULTIPART_OVERHEAD = 64 * 1024


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


async def read_upload(request: Request, _member: VerifiedMember) -> tuple[str, bytes]:
    max_bytes = request.state.document_limits.bytes
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "multipart/form-data":
        raise HTTPException(415, "Upload the PDF as multipart/form-data.")

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
    request: Request, db: Db, member: VerifiedMember, upload: Annotated[tuple[str, bytes], Depends(read_upload)],
) -> QueuedDocument:
    filename, data = upload
    limits = request.state.document_limits
    try:
        pages = int(run_pdf_task("count", data, timeout=limits.timeout))
    except PdfTaskError as exc:
        raise HTTPException(422, str(exc)) from None
    if not 1 <= pages <= limits.pages:
        raise HTTPException(422, f"Upload a PDF with 1 to {limits.pages} pages.")
    result = QueuedDocument(id=enqueue_document(db, member.workspace_id, filename, data, pages))
    db.commit()
    return result


@router.get("")
def list_documents(db: Db, member: CurrentMember) -> list[DocumentStatus]:
    documents = db.scalars(
        select(Document).where(Document.workspace_id == member.workspace_id)
        .order_by(Document.created_at.desc(), Document.id)
    )
    return [DocumentStatus.model_validate(document) for document in documents]


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


@router.get("/{document_id}/pages/{page}.png", responses={200: {"content": {"image/png": {}}}})
def preview(document_id: UUID, page: int, request: Request, db: Db, member: CurrentMember) -> Response:
    owned_id = db.scalar(
        select(Document.id)
        .where(
            Document.id == document_id, Document.workspace_id == member.workspace_id,
            Document.status == "ready", Document.pages >= page, page >= 1,
        )
        .with_for_update(read=True)
    )
    if owned_id is None:
        raise HTTPException(404, "Document page not found.")
    png = db.scalar(select(PagePreview.png).where(PagePreview.document_id == owned_id, PagePreview.page == page))
    if png is None:
        data = db.scalar(select(DocumentFile.pdf).where(DocumentFile.document_id == owned_id))
        if data is None:
            raise HTTPException(404, "Document page not found.")
        try:
            png = run_pdf_task("preview", data, str(page), timeout=request.state.document_limits.timeout)
        except PdfTaskError:
            raise HTTPException(422, "This page could not be rendered.") from None
        db.execute(insert(PagePreview).values(document_id=owned_id, page=page, png=png).on_conflict_do_nothing())
        db.commit()
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, no-store"})
