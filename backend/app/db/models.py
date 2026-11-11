"""Persistent data for Sourced workspaces, sessions, documents and ingestion."""

from datetime import date, datetime
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint, Computed, DateTime, ForeignKey, ForeignKeyConstraint, Index,
    LargeBinary, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR
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
        UniqueConstraint("id", "workspace_id", name="uq_documents_workspace"),
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
    channel: Mapped[str] = mapped_column(String(64), primary_key=True)
    questions: Mapped[int]
    answered: Mapped[int]
    not_found: Mapped[int]
    blocked: Mapped[int]


class Widget(Base):
    __tablename__ = "widgets"
    __table_args__ = (UniqueConstraint("id", "workspace_id", name="uq_widgets_workspace"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    site_key: Mapped[str] = mapped_column(String(64), unique=True)
    secret_key_hash: Mapped[str | None] = mapped_column(String(64))
    access_mode: Mapped[str] = mapped_column(String(16))
    allowed_origins: Mapped[list[str]] = mapped_column(ARRAY(Text))
    provider: Mapped[str] = mapped_column(String(32))
    judge_enabled: Mapped[bool]
    title: Mapped[str] = mapped_column(String(100))
    greeting: Mapped[str] = mapped_column(Text)
    brand_color: Mapped[str] = mapped_column(String(7))
    fallback_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class WidgetDocument(Base):
    __tablename__ = "widget_documents"
    __table_args__ = (
        ForeignKeyConstraint(["widget_id", "workspace_id"], ["widgets.id", "widgets.workspace_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["document_id", "workspace_id"], ["documents.id", "documents.workspace_id"], ondelete="CASCADE"),
        Index("ix_widget_documents_document_id", "document_id"),
    )
    widget_id: Mapped[UUID] = mapped_column(primary_key=True)
    document_id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID]


class QuestionLimit(Base):
    __tablename__ = "question_limits"
    scope: Mapped[str] = mapped_column(String(20), primary_key=True)
    subject: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(primary_key=True, index=True)
    requests: Mapped[int]
