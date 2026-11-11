"""Track attempts for each Sourced document ingestion."""

import sqlalchemy as sa
from alembic import op

revision = "0002_ingestion_attempts"
down_revision = "0001_workspaces"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
    op.create_check_constraint("document_attempts", "documents", "attempts >= 0")
    op.execute("UPDATE documents SET attempts = 1 WHERE status = 'processing'")


def downgrade():
    op.drop_constraint("document_attempts", "documents", type_="check")
    op.drop_column("documents", "attempts")
