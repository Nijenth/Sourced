"""Configuration and request admission shared by Sourced chat channels."""

import hashlib
import hmac
import ipaddress
import math
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from os import environ
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import HTTPException, Request
from sqlalchemy import delete, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert

from .db import Db
from .db.models import QuestionLimit
from .errors import TOO_MANY_REQUESTS, CodedHTTPException
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
    login_per_email: int = 10
    login_per_ip: int = 50
    signups_per_ip: int = 10

    @classmethod
    def from_env(cls):
        # Render sets RENDER_EXTERNAL_URL to the service's public address; an explicit setting wins.
        origin = environ.get("SOURCED_PUBLIC_ORIGIN") or environ.get("RENDER_EXTERNAL_URL")
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
            positive_int("SOURCED_LOGIN_ATTEMPTS_PER_EMAIL", 10),
            positive_int("SOURCED_LOGIN_ATTEMPTS_PER_IP", 50),
            positive_int("SOURCED_SIGNUPS_PER_IP", 10),
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


Reservation = tuple[str, str, datetime]
# Counters handed back when a question fails because the AI provider failed: the visitor and the
# workspace's daily allowance aren't spent on answers nobody got. The widget's per-minute counter is
# kept, so an outage can't turn into an unthrottled stream of provider calls.
REFUNDABLE_SCOPES = frozenset({"ip_minute", "workspace_day"})


def reserve(
    db: Db, limits: list[tuple[str, str, datetime, int]], message: str, *, minutes_in_message: bool = False,
) -> list[Reservation]:
    """Count one request against every limit, or count none and raise 429 if any limit is reached."""
    now = datetime.now(timezone.utc)
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
            if minutes_in_message:
                minutes = math.ceil(seconds / 60)
                message = message.format(when="in 1 minute" if minutes == 1 else f"in {minutes} minutes")
            raise CodedHTTPException(429, TOO_MANY_REQUESTS, message, headers={"Retry-After": str(seconds)})

    key = (QuestionLimit.scope, QuestionLimit.subject, QuestionLimit.expires_at)
    expired = select(*key).where(QuestionLimit.expires_at <= now).limit(100).with_for_update(skip_locked=True)
    db.execute(delete(QuestionLimit).where(tuple_(*key).in_(expired)))
    db.commit()
    return [(scope, subject, expires) for scope, subject, expires, _cap in limits]


def release(db: Db, reservations: list[Reservation], scopes: frozenset[str] | None = None) -> None:
    """Hand back reserved requests (all of them, or only those in `scopes`)."""
    db.rollback()
    for scope, subject, expires in reservations:
        if scopes is None or scope in scopes:
            db.execute(
                update(QuestionLimit)
                .where(QuestionLimit.scope == scope, QuestionLimit.subject == subject,
                       QuestionLimit.expires_at == expires, QuestionLimit.requests > 0)
                .values(requests=QuestionLimit.requests - 1)
            )
    db.commit()


def reserve_question(
    db: Db, settings: ChannelSettings, workspace_id: UUID, widget_id: UUID | None = None, ip: str = "",
) -> list[Reservation]:
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
    return reserve(db, limits, "Too many questions. Please try again later.")


def refund_failed_question(db: Db, reservations: list[Reservation]) -> None:
    release(db, reservations, REFUNDABLE_SCOPES)


# Sign-in protection: fixed windows, counted per email and per network address.
LOGIN_WINDOW = timedelta(minutes=15)
SIGNUP_WINDOW = timedelta(hours=1)
_process_key = secrets.token_bytes(32)


def _window_end(now: datetime, window: timedelta) -> datetime:
    seconds = int(window.total_seconds())
    return datetime.fromtimestamp((int(now.timestamp()) // seconds + 1) * seconds, timezone.utc)


def _fingerprint(settings: ChannelSettings, purpose: str, value: str) -> str:
    # Keyed, so stored rows don't reveal emails or addresses. Without SOURCED_PREVIEW_SECRET the key
    # is per process: limits still work but reset on restart and aren't shared between servers.
    key = settings.preview_secret or _process_key
    return hmac.new(key, f"{purpose}:{value}".encode(), hashlib.sha256).hexdigest()


def request_address(request: Request) -> str:
    try:
        return client_ip(request)
    except HTTPException:
        return "unknown"


def reserve_login(db: Db, settings: ChannelSettings, request: Request, email: str) -> list[Reservation]:
    expires = _window_end(datetime.now(timezone.utc), LOGIN_WINDOW)
    return reserve(db, [
        ("login_email", _fingerprint(settings, "login-email", email), expires, settings.login_per_email),
        ("login_ip", _fingerprint(settings, "login-ip", request_address(request)), expires, settings.login_per_ip),
    ], "Too many sign-in attempts. Try again {when}.", minutes_in_message=True)


def reserve_signup(db: Db, settings: ChannelSettings, request: Request) -> list[Reservation]:
    expires = _window_end(datetime.now(timezone.utc), SIGNUP_WINDOW)
    return reserve(db, [
        ("signup_ip", _fingerprint(settings, "signup-ip", request_address(request)), expires, settings.signups_per_ip),
    ], "Too many new accounts from this network. Try again {when}.", minutes_in_message=True)


def forgive_login(db: Db, reservations: list[Reservation]) -> None:
    """A successful sign-in doesn't count against the account's failed-attempt allowance."""
    release(db, reservations, frozenset({"login_email"}))
