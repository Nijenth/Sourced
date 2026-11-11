"""Version widget access so changing modes revokes existing grants."""

import sqlalchemy as sa
from alembic import op

revision = "0006_widget_session_tokens"
down_revision = "0005_hosted_widget_opt_in"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("widgets", sa.Column("access_version", sa.Integer(), nullable=False, server_default="0"))
    op.create_check_constraint("widget_access_mode", "widgets", "access_mode IN ('public', 'signed_in')")


def downgrade():
    op.drop_constraint("widget_access_mode", "widgets", type_="check")
    op.drop_column("widgets", "access_version")
