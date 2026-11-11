"""Postgres engine and session dependencies for Sourced."""

import os
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session


def database_engine(url: str | URL | None = None):
    url = url or os.environ.get("SOURCED_DATABASE_URL")
    if not url:
        raise RuntimeError("Set SOURCED_DATABASE_URL to the Sourced Postgres connection URL.")
    target = make_url(url)
    if target.drivername not in ("postgresql", "postgresql+psycopg"):
        raise RuntimeError("SOURCED_DATABASE_URL must use Postgres with psycopg.")
    return create_engine(target.set(drivername="postgresql+psycopg"), pool_pre_ping=True, hide_parameters=True)


def get_db(request: Request):
    with request.state.sessions() as db:
        yield db


Db = Annotated[Session, Depends(get_db)]
