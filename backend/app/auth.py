"""Sourced admin accounts and workspace-bound cookie sessions."""

import hashlib
import secrets
from os import environ
from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID, uuid4

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, StringConstraints
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert

from .channels import forgive_login, reserve_login, reserve_signup
from .db import Db
from .db.models import LoginSession, Membership, User, Workspace

router = APIRouter(prefix="/auth", tags=["auth"])
SESSION_COOKIE = "__Host-sourced_session"
CSRF_COOKIE = "__Host-sourced_csrf"
CSRF_HEADER = "X-CSRF-Token"
SESSION_TTL = timedelta(hours=12)
passwords = PasswordHasher()


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: SecretStr = Field(min_length=1, max_length=128)


class Signup(Credentials):
    password: SecretStr = Field(min_length=12, max_length=128)
    workspace_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120, pattern=r"^[^\x00]+$")
    ] = "My workspace"


class Member(BaseModel):
    id: UUID
    email: str
    workspace_id: UUID
    workspace_name: str
    role: str


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def session_hash(request: Request) -> str:
    token = request.cookies.get(SESSION_COOKIE, "")
    if len(token) != 43:
        raise HTTPException(401, "Sign in to Sourced.")
    return digest(token)


def get_member(request: Request, response: Response, db: Db) -> Member:
    statement = (
        select(User.id, User.email, Workspace.id.label("workspace_id"), Workspace.name.label("workspace_name"), Membership.role)
        .select_from(LoginSession)
        .join(Membership, (Membership.user_id == LoginSession.user_id) & (Membership.workspace_id == LoginSession.workspace_id))
        .join(User, User.id == Membership.user_id)
        .join(Workspace, Workspace.id == Membership.workspace_id)
        .where(LoginSession.token_hash == session_hash(request), LoginSession.expires_at > func.now())
    )
    row = db.execute(statement).mappings().one_or_none()
    if row is None:
        raise HTTPException(401, "Sign in to Sourced.")
    if row["role"] != "admin":
        raise HTTPException(403, "Workspace administrator access is required.")
    response.headers["Cache-Control"] = "private, no-store"
    return Member.model_validate(row)


CurrentMember = Annotated[Member, Depends(get_member)]


def require_csrf(request: Request, db: Db, member: CurrentMember) -> Member:
    token = request.headers.get(CSRF_HEADER, "")
    expected = db.scalar(select(LoginSession.csrf_hash).where(LoginSession.token_hash == session_hash(request)))
    if len(token) != 43 or expected is None or not secrets.compare_digest(digest(token), expected):
        raise HTTPException(403, "A valid CSRF token is required.")
    return member


VerifiedMember = Annotated[Member, Depends(require_csrf)]


def require_json(request: Request) -> None:
    # Browser forms cannot send JSON and cross-origin JSON requests require a CORS preflight.
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise HTTPException(415, "Send the credentials as JSON.")


def issue_session(db: Db, response: Response, member: Member) -> Member:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    db.add(LoginSession(
        token_hash=digest(token), csrf_hash=digest(csrf), user_id=member.id, workspace_id=member.workspace_id,
        expires_at=datetime.now(timezone.utc) + SESSION_TTL,
    ))
    db.commit()
    options = {"secure": True, "samesite": "strict", "max_age": int(SESSION_TTL.total_seconds())}
    response.set_cookie(SESSION_COOKIE, token, httponly=True, **options)
    response.set_cookie(CSRF_COOKIE, csrf, **options)
    response.headers["Cache-Control"] = "no-store"
    return member


@router.post("/signup", status_code=201, dependencies=[Depends(require_json)])
def signup(body: Signup, request: Request, db: Db, response: Response) -> Member:
    if environ.get("SOURCED_ALLOW_SIGNUP", "1") != "1":
        raise HTTPException(403, "New accounts are closed on this server. Ask the administrator for access.")
    reserve_signup(db, request.state.channels, request)
    email = body.email.casefold()
    user_id = db.scalar(
        insert(User)
        .values(id=uuid4(), email=email, password_hash=passwords.hash(body.password.get_secret_value()))
        .on_conflict_do_nothing(index_elements=[User.email])
        .returning(User.id)
    )
    if user_id is None:
        raise HTTPException(409, "An account with this email already exists.")
    workspace = Workspace(name=body.workspace_name)
    db.add(workspace)
    db.flush()
    db.add(Membership(user_id=user_id, workspace_id=workspace.id, role="admin"))
    db.flush()
    return issue_session(db, response, Member(
        id=user_id, email=email, workspace_id=workspace.id, workspace_name=workspace.name, role="admin",
    ))


@router.post("/login", dependencies=[Depends(require_json)])
def login(body: Credentials, request: Request, db: Db, response: Response) -> Member:
    email = body.email.casefold()
    attempt = reserve_login(db, request.state.channels, request, email)
    row = db.execute(
        select(User, Workspace, Membership.role)
        .join(Membership, Membership.user_id == User.id)
        .join(Workspace, Workspace.id == Membership.workspace_id)
        .where(User.email == email, Membership.role == "admin")
        .order_by(Workspace.created_at, Workspace.id)
        .limit(1)
    ).first()
    password = body.password.get_secret_value()
    invalid = HTTPException(401, "Email or password is incorrect.")
    if row is None:
        passwords.hash(password)
        raise invalid
    user, workspace, role = row
    try:
        passwords.verify(user.password_hash, password)
    except VerifyMismatchError:
        raise invalid from None
    member = Member(id=user.id, email=user.email, workspace_id=workspace.id, workspace_name=workspace.name, role=role)
    verified_hash = user.password_hash
    forgive_login(db, attempt)
    # Lock the account and confirm the password is still current before creating the session: if it was
    # changed since it was checked above, sessions are being revoked and this one mustn't slip through (N04).
    user = db.scalar(select(User).where(User.id == member.id).with_for_update())
    if user is None:
        raise invalid
    if user.password_hash != verified_hash:
        try:
            passwords.verify(user.password_hash, password)
        except VerifyMismatchError:
            raise invalid from None
    if passwords.check_needs_rehash(user.password_hash):
        user.password_hash = passwords.hash(password)
    return issue_session(db, response, member)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Db, _member: VerifiedMember) -> Response:
    db.execute(delete(LoginSession).where(LoginSession.token_hash == session_hash(request)))
    db.commit()
    response.delete_cookie(SESSION_COOKIE, secure=True, httponly=True, samesite="strict")
    response.delete_cookie(CSRF_COOKIE, secure=True, samesite="strict")
    response.status_code = 204
    return response


@router.get("/me")
def me(member: CurrentMember, response: Response) -> Member:
    response.headers["Cache-Control"] = "no-store"
    return member
