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

Network = ipaddress.IPv4Network | ipaddress.IPv6Network
Address = ipaddress.IPv4Address | ipaddress.IPv6Address


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
    trusted_proxies: tuple[Network, ...]

    @classmethod
    def from_env(cls):
        origin = environ.get("SOURCED_PUBLIC_ORIGIN")
        origin = canonical_origin(origin) if origin else None
        if origin and origin.startswith("http:") and urlsplit(origin).hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("SOURCED_PUBLIC_ORIGIN must use HTTPS outside loopback.")
        secret = environ.get("SOURCED_PREVIEW_SECRET", "").encode()
        if secret and len(secret) < 32:
            raise ValueError("SOURCED_PREVIEW_SECRET must be at least 32 bytes.")
        try:
            proxies = tuple(
                ipaddress.ip_network(item.strip())
                for item in environ.get("SOURCED_TRUSTED_PROXIES", "").split(",") if item.strip()
            )
        except ValueError:
            raise ValueError("SOURCED_TRUSTED_PROXIES must contain IP addresses or CIDR networks.") from None
        return cls(
            origin, secret,
            positive_int("SOURCED_WORKSPACE_QUESTIONS_PER_DAY", 300),
            positive_int("SOURCED_WIDGET_REQUESTS_PER_MINUTE", 20),
            positive_int("SOURCED_IP_REQUESTS_PER_MINUTE", 10),
            proxies,
        )


def public_settings(request: Request) -> ChannelSettings:
    settings = request.state.channels
    if not settings.public_origin or not settings.preview_secret:
        raise HTTPException(503, "Public chat is not configured.")
    return settings


def _address(value: str) -> Address:
    if "%" in value:
        raise ValueError("Scoped IP address")
    return ipaddress.ip_address(value)


def _trusted(address: Address, networks: tuple[Network, ...]) -> bool:
    mapped = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) else None
    return any(address in network or (mapped is not None and mapped in network) for network in networks)


def client_ip(request: Request) -> str:
    """Return the client identity shared by rate limits and signed previews."""
    try:
        peer = _address(request.client.host if request.client else "")
    except ValueError:
        raise HTTPException(503, "Could not verify the network address.") from None

    address = peer
    proxies = request.state.channels.trusted_proxies
    if _trusted(peer, proxies):
        forwarded = ",".join(request.headers.getlist("x-forwarded-for"))
        for value in reversed(forwarded.split(",")):
            try:
                hop = _address(value.strip())
            except ValueError:
                # An ambiguous trusted suffix cannot establish a different client.
                break
            if not _trusted(hop, proxies):
                address = hop
                break

    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return address.ipv4_mapped.compressed
        return ipaddress.ip_network((address, 64), strict=False).with_prefixlen
    return address.compressed


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
