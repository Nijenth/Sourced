"""Make hosted widget pages opt-in."""

import sqlalchemy as sa
from alembic import op

revision = "0005_hosted_widget_opt_in"
down_revision = "0004_widgets"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("widgets", sa.Column("hosted_page_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column("widgets", "hosted_page_enabled")
