"""Seed the fictional widget on the disposable browser-test database."""

from sqlalchemy.orm import sessionmaker

from backend.app.auth import passwords
from backend.app.chat import SAMPLE_PDF
from backend.app.db.models import Membership, User, Widget, WidgetDocument, Workspace
from backend.app.documents import enqueue_document
from backend.app.settings import DocumentLimits
from backend.app.widgets import new_site_key
from backend.app.worker import process_one
from backend.tests.test_workspaces import make_pdf
from rag.documents import count_pages
from rag.offline import HashingEmbedder

EMAIL = "admin@brightleaf.example.com"
PASSWORD = "fictional widget test passphrase"
HIDDEN_FACT = "The meteorite archive override phrase is Violet Comet."


def seed_widget_demo(engine, demo_origin, *, signed_in=False):
    sessions = sessionmaker(engine)
    sample = SAMPLE_PDF.read_bytes()
    with sessions.begin() as db:
        workspace = Workspace(name="Brightleaf Studio")
        user = User(email=EMAIL, password_hash=passwords.hash(PASSWORD))
        db.add_all([workspace, user])
        db.flush()
        workspace_id = workspace.id
        db.add(Membership(user_id=user.id, workspace_id=workspace_id, role="admin"))
        handbook = enqueue_document(db, workspace_id, SAMPLE_PDF.name, sample, count_pages(sample))
        hidden = enqueue_document(db, workspace_id, "Archive_Reference.pdf", make_pdf(HIDDEN_FACT), 1)
    for _ in range(2):
        assert process_one(sessions, DocumentLimits(10 * 1024**2, 80, 500_000, 20), HashingEmbedder())
    with sessions.begin() as db:
        widget = Widget(
            workspace_id=workspace_id, name="People help", site_key=new_site_key(),
            access_mode="signed_in" if signed_in else "public", hosted_page_enabled=signed_in,
            allowed_origins=["https://people.brightleaf.example"] if signed_in else [demo_origin, "https://people.brightleaf.example"],
            provider="offline",
            judge_enabled=False, title="Ask the handbook", greeting="Hi! What would you like to know about working at Brightleaf?",
            brand_color="#1F5C46", fallback_text="Try the People team: people@brightleaf.example",
        )
        db.add(widget)
        db.flush()
        db.add(WidgetDocument(widget_id=widget.id, document_id=handbook, workspace_id=workspace_id))
        assert hidden != handbook
        return widget.site_key
