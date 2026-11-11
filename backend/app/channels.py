"""Configuration and request admission shared by Sourced chat channels."""

import hashlib
import hmac
import ipaddress
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from os import environ
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import HTTPException, Request
from sqlalchemy import delete, select, tuple_
from sqlalchemy.dialects.postgresql import insert

from .db import Db
from .db.models import QuestionLimit
from .settings import positive_int


def canonical_origin(value: str) -> str:
    if not value.isascii() or any(c.isspace() or ord(c) < 32 or c in "\\%?#" for c in value):
        raise ValueError("Enter a complete website origin, such as https://example.com.")
    parsed = urlsplit(value)
    host = parsed.hostname
    if parsed.scheme not in {"http", "https"} or not host or parsed.username is not None or parsed.path not in {"", "/"}:
        raise ValueError("Enter a website origin without a path, such as https://example.com.")
    try:
        ip = ipaddress.ip_address(host)
        host = f"[{ip.compressed}]" if ip.version == 6 else ip.compressed
    except ValueError:
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host) or ".." in host:
            raise ValueError("Enter a valid website hostname.") from None
    port = parsed.port
    default = 443 if parsed.scheme == "https" else 80
    return f"{parsed.scheme}://{host}" + (f":{port}" if port is not None and port != default else "")


@dataclass(frozen=True)
class ChannelSettings:
    public_origin: str | None
    preview_secret: bytes
    workspace_daily: int
    widget_minute: int
    ip_minute: int

    @classmethod
    def from_env(cls):
        origin = environ.get("SOURCED_PUBLIC_ORIGIN")
        origin = canonical_origin(origin) if origin else None
        if origin and origin.startswith("http:") and urlsplit(origin).hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("SOURCED_PUBLIC_ORIGIN must use HTTPS outside loopback.")
        secret = environ.get("SOURCED_PREVIEW_SECRET", "").encode()
        if secret and len(secret) < 32:
            raise ValueError("SOURCED_PREVIEW_SECRET must be at least 32 bytes.")
        return cls(
            origin, secret,
            positive_int("SOURCED_WORKSPACE_QUESTIONS_PER_DAY", 300),
            positive_int("SOURCED_WIDGET_REQUESTS_PER_MINUTE", 20),
            positive_int("SOURCED_IP_REQUESTS_PER_MINUTE", 10),
        )


def public_settings(request: Request) -> ChannelSettings:
    settings = request.state.channels
    if not settings.public_origin or not settings.preview_secret:
        raise HTTPException(503, "Public chat is not configured.")
    return settings


def client_ip(request: Request) -> str:
    try:
        return ipaddress.ip_address(request.client.host if request.client else "").compressed
    except ValueError:
        raise HTTPException(503, "Could not verify the network address.") from None


def reserve_question(db: Db, settings: ChannelSettings, workspace_id: UUID, widget_id: UUID | None = None, ip: str = ""):
    now = datetime.now(timezone.utc)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    next_minute = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limits = [("workspace_day", str(workspace_id), midnight, settings.workspace_daily)]
    if widget_id is not None:
        fingerprint = hmac.new(settings.preview_secret, f"rate:{ip}".encode(), hashlib.sha256).hexdigest()
        limits += [
            ("widget_minute", str(widget_id), next_minute, settings.widget_minute),
            ("ip_minute", fingerprint, next_minute, settings.ip_minute),
        ]
    for scope, subject, expires, cap in limits:
        admitted = db.scalar(
            insert(QuestionLimit).values(scope=scope, subject=subject, expires_at=expires, requests=1)
            .on_conflict_do_update(
                index_elements=[QuestionLimit.scope, QuestionLimit.subject, QuestionLimit.expires_at],
                set_={"requests": QuestionLimit.requests + 1},
                where=QuestionLimit.requests < cap,
            ).returning(QuestionLimit.requests)
        )
        if admitted is None:
            db.rollback()
            seconds = max(1, math.ceil((expires - now).total_seconds()))
            raise HTTPException(429, "Too many questions. Please try again later.", headers={"Retry-After": str(seconds)})

    key = (QuestionLimit.scope, QuestionLimit.subject, QuestionLimit.expires_at)
    expired = select(*key).where(QuestionLimit.expires_at <= now).limit(100).with_for_update(skip_locked=True)
    db.execute(delete(QuestionLimit).where(tuple_(*key).in_(expired)))
    db.commit()
