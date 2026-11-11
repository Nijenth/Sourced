"""Add website widgets, workspace-safe document links, and question counters."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision = "0004_widgets"
down_revision = "0003_usage_daily"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("usage_daily", "channel", type_=sa.String(64), existing_type=sa.String(32))
    op.create_unique_constraint("uq_documents_workspace", "documents", ["id", "workspace_id"])
    op.create_table(
        "widgets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("site_key", sa.String(64), nullable=False, unique=True),
        sa.Column("secret_key_hash", sa.String(64)),
        sa.Column("access_mode", sa.String(16), nullable=False),
        sa.Column("allowed_origins", ARRAY(sa.Text()), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("judge_enabled", sa.Boolean(), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("greeting", sa.Text(), nullable=False),
        sa.Column("brand_color", sa.String(7), nullable=False),
        sa.Column("fallback_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("id", "workspace_id", name="uq_widgets_workspace"),
    )
    op.create_index("ix_widgets_workspace_id", "widgets", ["workspace_id"])
    op.create_table(
        "widget_documents",
        sa.Column("widget_id", sa.Uuid(), primary_key=True),
        sa.Column("document_id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["widget_id", "workspace_id"], ["widgets.id", "widgets.workspace_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id", "workspace_id"], ["documents.id", "documents.workspace_id"], ondelete="CASCADE"),
    )
    op.create_index("ix_widget_documents_document_id", "widget_documents", ["document_id"])
    op.create_table(
        "question_limits",
        sa.Column("scope", sa.String(20), primary_key=True),
        sa.Column("subject", sa.String(64), primary_key=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("requests", sa.Integer(), nullable=False),
    )
    op.create_index("ix_question_limits_expires_at", "question_limits", ["expires_at"])


def downgrade():
    op.drop_table("question_limits")
    op.drop_table("widget_documents")
    op.drop_table("widgets")
    op.drop_constraint("uq_documents_workspace", "documents", type_="unique")
    # Widget activity cannot be represented by the earlier admin-only schema.
    op.execute("DELETE FROM usage_daily WHERE channel LIKE 'widget:%'")
    op.alter_column("usage_daily", "channel", type_=sa.String(32), existing_type=sa.String(64))
