"""Cookie-free website chat and short-lived previews for cited pages."""

import base64
import binascii
import hashlib
import hmac
import json
import re
import time
from os import environ
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select

from rag.pipeline import NOT_FOUND_TEXT
from rag.providers import ProviderError, make_embedder

from .channels import client_ip, public_settings, reserve_question
from .common import provider_failure, resolve_provider
from .db import Db
from .db.models import Document, Widget
from .documents import page_png
from .schemas import ChatResponse, PublicChatRequest
from .store import KeywordSearchLimit, PgVectorStore
from .usage import record_answer
from .widget_assets import ASSET_PREFIX, ROOT, page_headers, script_tag
from .widgets import document_ids

api = APIRouter(prefix="/api/v1/public", tags=["public"])
pages = APIRouter(include_in_schema=False)
PREVIEW_SECONDS = 600
KEY = re.compile(r"pk_[A-Za-z0-9_-]{32}")
BLOCKED = "I couldn't verify an answer against the documents, so I'm not showing one."


class PublicConfig(BaseModel):
    title: str
    greeting: str
    brand_color: str
    fallback_text: str
    access_mode: str


def public_widget(db: Db, site_key: str, lock: bool = False) -> Widget:
    if not KEY.fullmatch(site_key):
        raise HTTPException(404, "Widget not found.")
    query = select(Widget).where(Widget.site_key == site_key, Widget.access_mode == "public")
    widget = db.scalar(query.with_for_update(read=True) if lock else query)
    if widget is None:
        raise HTTPException(404, "Widget not found.")
    return widget


def origin_is_allowed(origin: str, widget: Widget, sourced_origin: str | None) -> bool:
    if origin == sourced_origin:
        return widget.hosted_page_enabled
    return origin in widget.allowed_origins


def allowed_origin(request: Request, widget: Widget, required: bool = True) -> str | None:
    origin = request.headers.get("origin")
    if origin is None and not required:
        return None
    if origin is None or not origin_is_allowed(origin, widget, request.state.channels.public_origin):
        raise HTTPException(403, "This website is not allowed to use the widget.")
    return origin


def cors(origin: str | None) -> dict[str, str]:
    headers = {"Vary": "Origin", "Cache-Control": "no-store"}
    if origin is not None:
        headers["Access-Control-Allow-Origin"] = origin
    return headers


@api.get("/widgets/{site_key}/config")
def config(site_key: str, request: Request, response: Response, db: Db) -> PublicConfig:
    public_settings(request)
    widget = public_widget(db, site_key)
    response.headers.update(cors(allowed_origin(request, widget, required=False)))
    return PublicConfig.model_validate(widget, from_attributes=True)


@api.options("/chat", include_in_schema=False)
def preflight(site_key: str, request: Request, db: Db) -> Response:
    public_settings(request)
    origin = allowed_origin(request, public_widget(db, site_key))
    method = request.headers.get("access-control-request-method")
    headers = {name.strip().lower() for name in request.headers.get("access-control-request-headers", "").split(",")}
    if method != "POST" or not headers <= {"content-type", "x-sourced-key"}:
        raise HTTPException(403, "This cross-origin request is not allowed.")
    return Response(status_code=204, headers={
        **cors(origin), "Access-Control-Allow-Methods": "POST", "Access-Control-Allow-Headers": "Content-Type, X-Sourced-Key",
    })


def signature(secret: bytes, ip: str, payload: str) -> str:
    return hmac.new(secret, f"preview:{ip}:{payload}".encode(), hashlib.sha256).hexdigest()


def signed_preview(secret: bytes, ip: str, site_key: str, origin: str, document_id: str, page: int) -> str:
    payload = json.dumps([site_key, origin, document_id, page, int(time.time()) + PREVIEW_SECONDS], separators=(",", ":"))
    encoded = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    token = encoded + "." + signature(secret, ip, encoded)
    return f"/api/v1/public/previews/{token}.png"


@api.post("/chat")
def chat(body: PublicChatRequest, request: Request, response: Response, db: Db, site_key: str | None = None) -> ChatResponse:
    settings = public_settings(request)
    key = request.headers.get("x-sourced-key", "")
    if site_key is not None and site_key != key:
        raise HTTPException(404, "Widget not found.")
    widget = public_widget(db, key, lock=True)
    origin = allowed_origin(request, widget)
    headers = cors(origin)
    try:
        ip = client_ip(request)
        workspace_id, widget_id, provider, judge = widget.workspace_id, widget.id, widget.provider, widget.judge_enabled
        selected = set(document_ids(db, widget))
        reserve_question(db, settings, workspace_id, widget_id, ip)
        answer = ChatResponse(status="not_found", text=NOT_FOUND_TEXT)
        if selected:
            store = PgVectorStore(request.state.sessions, make_embedder(), workspace_id, selected)
            answer = request.state.chat_service.ask(
                store, body.question, [message.model_dump() for message in body.history], resolve_provider(provider),
                judge_enabled=judge, public=True,
            )
            if judge and answer.status == "answered" and (not answer.judgement or answer.judgement.verdict != "supported"):
                answer = ChatResponse(status="unverified", text=BLOCKED)

        current = public_widget(db, key, lock=True)
        allowed_origin(request, current)
        cited = {UUID(citation.document_id) for citation in answer.citations}
        if cited:
            attached = set(document_ids(db, current))
            ready = set(db.scalars(select(Document.id).where(
                Document.id.in_(cited), Document.workspace_id == workspace_id, Document.status == "ready",
            ).with_for_update(read=True)))
            if not cited <= attached or cited != ready:
                answer = ChatResponse(status="not_found", text=NOT_FOUND_TEXT)
        if answer.status == "answered":
            for citation in answer.citations:
                citation.preview_url = signed_preview(settings.preview_secret, ip, key, origin, citation.document_id, citation.page)
            if answer.judgement:
                answer.judgement.problems = []
        else:
            answer = ChatResponse(
                status=answer.status, text=NOT_FOUND_TEXT if answer.status == "not_found" else BLOCKED,
                fallback=current.fallback_text,
            )
        response.headers.update(headers)
        return record_answer(db, workspace_id, answer, channel=f"widget:{widget_id}")
    except (ProviderError, KeywordSearchLimit) as exc:
        error = provider_failure() if isinstance(exc, ProviderError) else HTTPException(503, "Chat is temporarily unavailable.")
        error.headers = headers
        raise error from None
    except HTTPException as exc:
        exc.headers = {**headers, **(exc.headers or {})}
        raise


@api.get("/previews/{signed_id}.png")
def preview(signed_id: str, request: Request, db: Db) -> Response:
    settings = public_settings(request)
    encoded, separator, tag = signed_id.partition(".")
    if len(signed_id) > 800 or not separator or not re.fullmatch(r"[0-9a-f]{64}", tag) or not hmac.compare_digest(
        tag, signature(settings.preview_secret, client_ip(request), encoded),
    ):
        raise HTTPException(404, "Preview not found.")
    try:
        site_key, granted_origin, document_id, page, expires = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        document_id = UUID(document_id)
    except (ValueError, TypeError, binascii.Error):
        raise HTTPException(404, "Preview not found.") from None
    if expires <= time.time():
        raise HTTPException(404, "Preview expired.")
    widget = public_widget(db, site_key, lock=True)
    origin = request.headers.get("origin")
    if (
        not origin_is_allowed(granted_origin, widget, settings.public_origin)
        or (origin is not None and origin != granted_origin)
        or document_id not in document_ids(db, widget)
    ):
        raise HTTPException(404, "Preview not found.")
    png = page_png(db, widget.workspace_id, document_id, page, request.state.document_limits.timeout)
    return Response(png, media_type="image/png", headers={**cors(origin), "Referrer-Policy": "no-referrer"})


@pages.get("/c/{site_key}")
def hosted(site_key: str, request: Request, db: Db) -> HTMLResponse:
    if not public_widget(db, site_key).hosted_page_enabled:
        raise HTTPException(404, "Widget not found.")
    settings = public_settings(request)
    tag = script_tag(settings.public_origin, site_key, hosted=True)
    return HTMLResponse(
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>Sourced chat</title><link rel="stylesheet" href="{ASSET_PREFIX}/widget.css"><body class="sourced-hosted">{tag}</body></html>',
        headers=page_headers(settings.public_origin, hosted=True),
    )


@pages.get("/demo/")
def demo(request: Request, db: Db) -> HTMLResponse:
    site_key = environ.get("SOURCED_DEMO_SITE_KEY")
    if not site_key:
        raise HTTPException(404)
    settings = public_settings(request)
    public_widget(db, site_key)
    template = (ROOT / "demo" / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(template.replace("<!-- sourced-widget -->", script_tag(settings.public_origin, site_key)),
                        headers=page_headers(settings.public_origin))


@pages.get("/demo/site.css")
def demo_styles() -> Response:
    if not environ.get("SOURCED_DEMO_SITE_KEY"):
        raise HTTPException(404)
    return Response((ROOT / "demo" / "site.css").read_bytes(), media_type="text/css")
