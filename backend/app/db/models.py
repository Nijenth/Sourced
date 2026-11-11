"""Persistent data for Sourced workspaces, sessions, documents and ingestion."""

from datetime import date, datetime
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint, Computed, DateTime, ForeignKey, ForeignKeyConstraint, Index,
    LargeBinary, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map = {datetime: DateTime(timezone=True)}


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class User(Base):
    __tablename__ = "users"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Membership(Base):
    __tablename__ = "memberships"
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(32))


class LoginSession(Base):
    __tablename__ = "login_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "workspace_id"], ["memberships.user_id", "memberships.workspace_id"], ondelete="CASCADE"
        ),
    )
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[UUID]
    workspace_id: Mapped[UUID]
    expires_at: Mapped[datetime] = mapped_column(index=True)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("status IN ('queued', 'processing', 'ready', 'failed')", name="document_status"),
        CheckConstraint("progress BETWEEN 0 AND 100", name="document_progress"),
        CheckConstraint("attempts >= 0", name="document_attempts"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    pages: Mapped[int]
    status: Mapped[str] = mapped_column(String(16), index=True)
    progress: Mapped[int]
    attempts: Mapped[int] = mapped_column(server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    embedding_model: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    claim_id: Mapped[UUID | None]
    lease_until: Mapped[datetime | None]


class DocumentFile(Base):
    __tablename__ = "document_files"
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True)
    pdf: Mapped[bytes] = mapped_column(LargeBinary)


class DocumentChunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "engine_chunk_id"),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    engine_chunk_id: Mapped[str] = mapped_column(Text)
    page: Mapped[int]
    ordinal: Mapped[int]
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector())
    tsv: Mapped[str | None] = mapped_column(TSVECTOR, Computed("to_tsvector('english'::regconfig, text)", persisted=True))


class PagePreview(Base):
    __tablename__ = "page_previews"
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True)
    page: Mapped[int] = mapped_column(primary_key=True)
    png: Mapped[bytes] = mapped_column(LargeBinary)


class UsageDaily(Base):
    __tablename__ = "usage_daily"
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[date] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(32), primary_key=True)
    questions: Mapped[int]
    answered: Mapped[int]
    not_found: Mapped[int]
    blocked: Mapped[int]
