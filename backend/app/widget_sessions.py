"""Server-issued bearer credentials for signed-in Sourced widgets."""

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import unquote
from uuid import UUID

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, StringConstraints

from .db.models import Widget

SESSION_SECONDS = 15 * 60
SECRET_KEY = re.compile(r"sk_[A-Za-z0-9_-]{43}")
SESSION_TOKEN = re.compile(r"st_([A-Za-z0-9_-]{1,2048})\.([0-9a-f]{64})")
ACCESS_CREDENTIAL = re.compile(r"(?:sk_[A-Za-z0-9_-]{43}|st_[A-Za-z0-9_-]+\.[a-f0-9]{64})")
# A page preview's URL is itself a short-lived signed grant (security audit S08).
PREVIEW_GRANT = re.compile(r"(/previews/)[^/?#]+(\.png)")


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_ref: Annotated[str, StringConstraints(max_length=256, pattern=r"^[^\x00-\x1f\x7f]*$")] | None = None


class SessionResponse(BaseModel):
    token: str
    expires_in: int


def unauthorized(detail: str = "A valid widget session token is required.") -> HTTPException:
    return HTTPException(401, detail, headers={"WWW-Authenticate": "Bearer"})


def bearer(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise unauthorized()
    return token


def accepts_secret(widget: Widget, token: str) -> bool:
    digest = hashlib.sha256(token.encode()).hexdigest()
    matches = hmac.compare_digest(digest, widget.secret_key_hash or ("0" * 64))
    return matches and widget.secret_key_hash is not None and bool(SECRET_KEY.fullmatch(token))


def _sign(secret: bytes, widget: Widget, encoded: str) -> str:
    context = f"widget-session:{widget.id}:{widget.access_version}:{widget.site_key}:{widget.secret_key_hash}:{encoded}"
    return hmac.new(secret, context.encode(), hashlib.sha256).hexdigest()


def issue_session(secret: bytes, widget: Widget, user_ref: str | None) -> SessionResponse:
    claims = {"widget_id": str(widget.id), "exp": int(time.time()) + SESSION_SECONDS, "jti": secrets.token_urlsafe(16)}
    if user_ref is not None:
        claims["user_ref"] = user_ref
    payload = json.dumps(claims, ensure_ascii=False, separators=(",", ":"))
    encoded = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    return SessionResponse(token=f"st_{encoded}.{_sign(secret, widget, encoded)}", expires_in=SESSION_SECONDS)


@dataclass(frozen=True)
class PresentedSession:
    widget_id: UUID
    expires: int
    encoded: str
    tag: str

    @classmethod
    def parse(cls, token: str):
        match = SESSION_TOKEN.fullmatch(token)
        if not match:
            raise unauthorized()
        encoded, tag = match.groups()
        try:
            payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            widget_id = UUID(payload["widget_id"])
            expires = payload["exp"]
        except (ValueError, TypeError, AttributeError, KeyError, RecursionError):
            raise unauthorized() from None
        if type(expires) is not int or expires <= time.time():
            raise unauthorized()
        return cls(widget_id, expires, encoded, tag)

    def verify(self, secret: bytes, widget: Widget) -> str:
        if (
            widget.id != self.widget_id or widget.access_mode != "signed_in" or not widget.secret_key_hash
            or self.expires <= time.time() or not hmac.compare_digest(self.tag, _sign(secret, widget, self.encoded))
        ):
            raise unauthorized()
        # The signature is unique to this issued token and binds previews to that session.
        return hashlib.sha256(self.tag.encode()).hexdigest()


class RedactAccessCredentials(logging.Filter):
    """Prevent accidental URL credentials from entering Uvicorn's access logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) == 5:
            address, method, target, protocol, status = record.args
            if isinstance(target, str):
                # The normal fragment and Authorization header never reach the request target.
                if ACCESS_CREDENTIAL.search(unquote(target)):
                    target = ACCESS_CREDENTIAL.sub("[redacted]", unquote(target.split("?", 1)[0]))
                target = PREVIEW_GRANT.sub(r"\1[redacted]\2", target)
                record.args = (address, method, target, protocol, status)
        return True
