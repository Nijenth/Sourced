"""Count Sourced chat outcomes by workspace, channel, and UTC day."""

import sqlalchemy as sa
from alembic import op

revision = "0003_usage_daily"
down_revision = "0002_ingestion_attempts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "usage_daily",
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("channel", sa.String(32), primary_key=True),
        sa.Column("questions", sa.Integer(), nullable=False),
        sa.Column("answered", sa.Integer(), nullable=False),
        sa.Column("not_found", sa.Integer(), nullable=False),
        sa.Column("blocked", sa.Integer(), nullable=False),
    )


def downgrade():
    op.drop_table("usage_daily")
