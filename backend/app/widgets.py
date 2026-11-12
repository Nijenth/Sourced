"""Admin CRUD and rotation for Sourced website widgets."""

import hashlib
import secrets
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from sqlalchemy import delete, select

from .auth import CurrentMember, VerifiedMember
from .channels import canonical_origin, public_settings
from .common import resolve_provider
from .db import Db
from .db.models import Document, Widget, WidgetDocument
from .demo import demo_site_key
from .widget_assets import embed_details
from .widget_sessions import SessionRequest, SessionResponse, accepts_secret, bearer, issue_session, unauthorized

router = APIRouter(prefix="/widgets", tags=["widgets"])
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100, pattern=r"^[^\x00]+$")]
Copy = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500, pattern=r"^[^\x00]*$")]


class WidgetFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Label = "Website widget"
    access_mode: Literal["public", "signed_in"] = "public"
    allowed_origins: list[Annotated[str, StringConstraints(max_length=300)]] = Field(default_factory=list, max_length=25)
    document_ids: list[UUID] = Field(default_factory=list, max_length=100)
    provider: Annotated[str, StringConstraints(min_length=1, max_length=32)] | None = None
    judge_enabled: bool = False
    hosted_page_enabled: bool = False
    title: Label = "Ask the handbook"
    greeting: Copy = "Hi! What would you like to know?"
    brand_color: str = Field(default="#1F5C46", pattern=r"^#[a-fA-F0-9]{6}$")
    fallback_text: Copy = "Please contact our team for more help."

    @field_validator("allowed_origins")
    @classmethod
    def valid_origins(cls, values):
        return list(dict.fromkeys(canonical_origin(value.strip()) for value in values))


class WidgetSettings(WidgetFields):
    id: UUID
    site_key: str
    created_at: datetime
    script_url: str | None
    integrity: str | None
    hosted_url: str | None
    has_secret_key: bool
    demo_url: str | None = None


class RotateKeys(BaseModel):
    model_config = ConfigDict(extra="forbid")
    site_key: bool = True
    secret_key: bool = False


class RotatedKeys(BaseModel):
    site_key: str
    secret_key: str | None = None


def new_site_key() -> str:
    return "pk_" + secrets.token_urlsafe(24)


def document_ids(db: Db, widget: Widget) -> list[UUID]:
    return list(db.scalars(select(WidgetDocument.document_id).where(
        WidgetDocument.widget_id == widget.id, WidgetDocument.workspace_id == widget.workspace_id,
    ).order_by(WidgetDocument.document_id)))


def assign_documents(db: Db, widget: Widget, requested: list[UUID]) -> None:
    ids = set(requested)
    existing = set(document_ids(db, widget))
    if ids:
        rows = {id: status for id, status in db.execute(
            select(Document.id, Document.status)
            .where(Document.workspace_id == widget.workspace_id, Document.id.in_(ids))
            .with_for_update(read=True)
        )}
        if rows.keys() != ids:
            raise HTTPException(404, "Document not found.")
        if any(rows[doc_id] != "ready" for doc_id in ids - existing):
            raise HTTPException(422, "Choose documents that are ready.")
    db.execute(delete(WidgetDocument).where(
        WidgetDocument.widget_id == widget.id, WidgetDocument.workspace_id == widget.workspace_id,
        WidgetDocument.document_id.in_(existing - ids),
    ))
    db.add_all(WidgetDocument(widget_id=widget.id, document_id=id, workspace_id=widget.workspace_id) for id in ids - existing)


def owned_widget(db: Db, workspace_id: UUID, widget_id: UUID, write: bool = False) -> Widget:
    query = select(Widget).where(Widget.id == widget_id, Widget.workspace_id == workspace_id)
    widget = db.scalar(query.with_for_update() if write else query)
    if widget is None:
        raise HTTPException(404, "Widget not found.")
    return widget


def settings_response(db: Db, request: Request, widget: Widget) -> WidgetSettings:
    channels = request.state.channels
    return WidgetSettings(
        **{field: getattr(widget, field) for field in WidgetFields.model_fields if field != "document_ids"},
        document_ids=document_ids(db, widget), id=widget.id, site_key=widget.site_key, created_at=widget.created_at,
        has_secret_key=widget.secret_key_hash is not None,
        demo_url=(
            f"{channels.public_origin}/demo/"
            if channels.public_origin and channels.preview_secret and demo_site_key(db) == widget.site_key else None
        ),
        **embed_details(
            channels.public_origin if channels.preview_secret else None, widget.site_key, widget.hosted_page_enabled,
        ),
    )


@router.get("")
def list_widgets(db: Db, request: Request, member: CurrentMember) -> list[WidgetSettings]:
    widgets = db.scalars(select(Widget).where(Widget.workspace_id == member.workspace_id).order_by(Widget.created_at, Widget.id))
    return [settings_response(db, request, widget) for widget in widgets]


@router.post("", status_code=201)
def create_widget(body: WidgetFields, db: Db, request: Request, member: VerifiedMember) -> WidgetSettings:
    fields = body.model_dump(exclude={"document_ids", "provider"})
    provider = resolve_provider(body.provider)
    if "judge_enabled" not in body.model_fields_set:
        fields["judge_enabled"] = provider != "offline"
    if provider == "offline" and fields["judge_enabled"]:
        raise HTTPException(422, "Accuracy checking requires an AI answer model.")
    widget = Widget(workspace_id=member.workspace_id, site_key=new_site_key(), provider=provider, **fields)
    db.add(widget)
    db.flush()
    assign_documents(db, widget, body.document_ids)
    result = settings_response(db, request, widget)
    db.commit()
    return result


@router.get("/{widget_id}")
def get_widget(widget_id: UUID, db: Db, request: Request, member: CurrentMember) -> WidgetSettings:
    return settings_response(db, request, owned_widget(db, member.workspace_id, widget_id))


@router.patch("/{widget_id}")
def update_widget(widget_id: UUID, body: WidgetFields, db: Db, request: Request, member: VerifiedMember) -> WidgetSettings:
    widget = owned_widget(db, member.workspace_id, widget_id, write=True)
    fields = body.model_dump(exclude_unset=True)
    if "provider" in fields:
        fields["provider"] = resolve_provider(body.provider)
    if fields.get("provider", widget.provider) == "offline" and fields.get("judge_enabled", widget.judge_enabled):
        raise HTTPException(422, "Accuracy checking requires an AI answer model.")
    if "document_ids" in fields:
        assign_documents(db, widget, fields.pop("document_ids"))
    if fields.get("access_mode", widget.access_mode) != widget.access_mode:
        widget.access_version += 1
    for field, value in fields.items():
        setattr(widget, field, value)
    result = settings_response(db, request, widget)
    db.commit()
    return result


@router.delete("/{widget_id}", status_code=204)
def delete_widget(widget_id: UUID, db: Db, member: VerifiedMember) -> Response:
    db.delete(owned_widget(db, member.workspace_id, widget_id, write=True))
    db.commit()
    return Response(status_code=204)


@router.post("/{widget_id}/rotate-keys", response_model_exclude_none=True)
def rotate_keys(widget_id: UUID, body: RotateKeys, db: Db, member: VerifiedMember) -> RotatedKeys:
    widget = owned_widget(db, member.workspace_id, widget_id, write=True)
    if not body.site_key and not body.secret_key:
        raise HTTPException(422, "Choose a key to rotate.")
    if body.site_key:
        widget.site_key = new_site_key()
    secret_key = None
    if body.secret_key:
        secret_key = "sk_" + secrets.token_urlsafe(32)
        widget.secret_key_hash = hashlib.sha256(secret_key.encode()).hexdigest()
    result = RotatedKeys(site_key=widget.site_key, secret_key=secret_key)
    db.commit()
    return result


@router.post("/{widget_id}/session-tokens")
def session_tokens(widget_id: UUID, body: SessionRequest, request: Request, response: Response, db: Db) -> SessionResponse:
    token = bearer(request)
    widget = db.scalar(select(Widget).where(Widget.id == widget_id).with_for_update(read=True))
    if widget is None or not accepts_secret(widget, token):
        raise unauthorized("A valid widget server secret key is required.")
    if widget.access_mode != "signed_in":
        raise HTTPException(403, "This widget does not use signed-in access.")
    settings = public_settings(request)
    response.headers["Cache-Control"] = "no-store"
    return issue_session(settings.preview_secret, widget, body.user_ref)
