"""Create or update the fictional Brightleaf demo: an admin account, the sample handbook and a public widget.

Run with `python -m backend.app.seed_demo` (the deploy start script does this when SOURCED_DEMO_ADMIN_EMAIL is
set). It's safe to run on every start: existing records are reused, and only missing pieces are added.
"""

import logging
import sys
from hashlib import sha256
from os import environ

from argon2.exceptions import VerificationError
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from rag.documents import count_pages
from rag.providers import chat_provider_names

from .auth import passwords
from .chat import SAMPLE_PDF
from .db import database_engine
from .db.models import Document, Membership, User, Widget, WidgetDocument, Workspace
from .demo import DEMO_WIDGET_NAME, demo_admin_email
from .documents import enqueue_document
from .widgets import new_site_key

DEMO_WORKSPACE_NAME = "Brightleaf Studio (demo)"
logger = logging.getLogger(__name__)


def seed(sessions: sessionmaker, email: str, password: str, provider: str) -> str:
    """Make sure the demo exists and return its widget's public site key."""
    email = email.strip().casefold()
    data = SAMPLE_PDF.read_bytes()
    with sessions.begin() as db:
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(email=email, password_hash=passwords.hash(password))
            workspace = Workspace(name=DEMO_WORKSPACE_NAME)
            db.add_all([user, workspace])
            db.flush()
            db.add(Membership(user_id=user.id, workspace_id=workspace.id, role="admin"))
            workspace_id = workspace.id
        else:
            try:
                passwords.verify(user.password_hash, password)
            except VerificationError:
                user.password_hash = passwords.hash(password)  # the configured password wins
            workspace_id = db.scalar(
                select(Membership.workspace_id).where(Membership.user_id == user.id, Membership.role == "admin")
                .join(Workspace, Workspace.id == Membership.workspace_id).order_by(Workspace.created_at).limit(1)
            )
            if workspace_id is None:
                raise RuntimeError("The demo admin account exists but administers no workspace.")

        handbook = db.scalar(select(Document.id).where(
            Document.workspace_id == workspace_id, Document.sha256 == sha256(data).hexdigest(),
        ).limit(1))
        if handbook is None:
            handbook = enqueue_document(db, workspace_id, SAMPLE_PDF.name, data, count_pages(data))

        widget = db.scalar(select(Widget).where(Widget.workspace_id == workspace_id, Widget.name == DEMO_WIDGET_NAME))
        if widget is None:
            widget = Widget(
                workspace_id=workspace_id, name=DEMO_WIDGET_NAME, site_key=new_site_key(), access_mode="public",
                hosted_page_enabled=True, allowed_origins=[], provider=provider, judge_enabled=provider != "offline",
                title="Ask the handbook", greeting="Hi! Ask me anything about working at Brightleaf.",
                brand_color="#1F5C46", fallback_text="Try the People team: people@brightleaf.example",
            )
            db.add(widget)
            db.flush()
        else:
            widget.provider = provider
            widget.judge_enabled = provider != "offline"
        linked = db.scalar(select(WidgetDocument.document_id).where(
            WidgetDocument.widget_id == widget.id, WidgetDocument.document_id == handbook,
        ))
        if linked is None:
            db.add(WidgetDocument(widget_id=widget.id, document_id=handbook, workspace_id=workspace_id))
        return widget.site_key


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    email = demo_admin_email()
    password = environ.get("SOURCED_DEMO_ADMIN_PASSWORD", "")
    if not email or len(password) < 12:
        print("Set SOURCED_DEMO_ADMIN_EMAIL and SOURCED_DEMO_ADMIN_PASSWORD (12+ characters).", file=sys.stderr)
        return 2
    ready = chat_provider_names()
    provider = environ.get("SOURCED_DEMO_PROVIDER") or next(iter(ready), None)
    if provider not in ready:
        print("The demo needs an answer model. Configure a chat provider (for example GROQ_API_KEY).", file=sys.stderr)
        return 2
    engine = database_engine()
    try:
        site_key = seed(sessionmaker(engine), email, password, provider)
    finally:
        engine.dispose()
    logger.info("Demo ready: widget %s answers with %s. The worker indexes the handbook.", site_key, provider)
    return 0


if __name__ == "__main__":
    sys.exit(main())
