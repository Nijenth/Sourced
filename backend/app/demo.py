"""Which widget the public demo page (/demo/) shows."""

from os import environ

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db.models import Membership, User, Widget

DEMO_WIDGET_NAME = "Sourced demo"


def demo_admin_email() -> str | None:
    email = environ.get("SOURCED_DEMO_ADMIN_EMAIL", "").strip().casefold()
    return email or None


def demo_site_key(db: Session) -> str | None:
    """SOURCED_DEMO_SITE_KEY, or the seeded demo widget of SOURCED_DEMO_ADMIN_EMAIL's workspace."""
    key = environ.get("SOURCED_DEMO_SITE_KEY")
    if key:
        return key
    email = demo_admin_email()
    if email is None:
        return None
    return db.scalar(
        select(Widget.site_key)
        .join(Membership, (Membership.workspace_id == Widget.workspace_id) & (Membership.role == "admin"))
        .join(User, User.id == Membership.user_id)
        .where(User.email == email, Widget.name == DEMO_WIDGET_NAME, Widget.access_mode == "public")
        .order_by(Widget.created_at).limit(1)
    )
