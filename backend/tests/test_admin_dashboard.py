from datetime import datetime, timezone
from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image, ImageChops
from reportlab.pdfgen.canvas import Canvas

from backend.app.main import app
from backend.app.pdf_tasks import render_preview
from backend.app.settings import DocumentLimits
from backend.app.worker import process_one
from rag.offline import HashingEmbedder, KeywordChat

from .test_workspaces import ALPHA_TEXT, API, QUESTION, ask, csrf, make_pdf, queue, signup


def test_usage_counts_only_completed_outcomes_in_the_workspace(workspace_client, sessions, monkeypatch):
    client = workspace_client
    path = f"{API}/usage"
    assert client.get(path).status_code == 401
    signup(client, "alpha")
    assert client.get(path).json() == []
    assert ask(client).json()["status"] == "not_found"
    queue(client, make_pdf(ALPHA_TEXT))
    assert process_one(sessions, DocumentLimits.from_env(), HashingEmbedder())
    assert ask(client).json()["status"] == "answered"

    def ungrounded(self, system, messages, max_tokens=900):
        return '{"coverage":"full","answer":"Fabricated answer 99999 [1]."}'

    monkeypatch.setattr(KeywordChat, "complete", ungrounded)
    assert ask(client).json()["status"] == "unverified"
    assert client.post(f"{API}/chat", json={"question": QUESTION}).status_code == 403
    assert client.get(path + "?days=91").status_code == 422
    assert client.get(path + "?days=1").json() == [{
        "day": datetime.now(timezone.utc).date().isoformat(),
        "channel": "ask_test",
        "questions": 3,
        "answered": 1,
        "not_found": 1,
        "blocked": 1,
    }]
    with TestClient(app, base_url="https://sourced.example") as other:
        signup(other, "beta")
        assert other.get(path).json() == []


def test_upload_settings_pdf_and_quote_highlight_are_scoped(workspace_client, sessions):
    client = workspace_client
    settings_path = f"{API}/documents/settings"
    assert client.get(settings_path).status_code == 401
    signup(client, "alpha")
    assert client.get(settings_path).json() == {
        "max_bytes": 1024**2, "max_pages": 80, "embedding_provider": "Offline keyword matching",
    }
    pdf = make_pdf(ALPHA_TEXT)
    document_id = queue(client, pdf)
    assert process_one(sessions, DocumentLimits.from_env(), HashingEmbedder())
    path = f"{API}/documents/{document_id}"
    download = client.get(path + "/pdf")
    assert download.content == pdf and download.headers["content-type"] == "application/pdf"
    assert download.headers["cache-control"] == "private, no-store"

    base = client.get(path + "/pages/1.png")
    highlight_path = path + "/pages/1/highlight"
    assert client.post(highlight_path, json={"quote": ALPHA_TEXT}).status_code == 403
    highlighted = client.post(highlight_path, headers=csrf(client), json={"quote": ALPHA_TEXT})
    assert highlighted.status_code == 200, highlighted.text
    with Image.open(BytesIO(base.content)) as plain, Image.open(BytesIO(highlighted.content)) as marked:
        assert plain.size == marked.size
        assert ImageChops.difference(plain.convert("RGB"), marked.convert("RGB")).getbbox() is not None
    assert client.post(highlight_path, headers=csrf(client), json={"quote": "An absent phrase"}).content == base.content

    with TestClient(app, base_url="https://sourced.example") as other:
        signup(other, "beta")
        assert other.get(path + "/pdf").status_code == 404
        assert other.post(highlight_path, headers=csrf(other), json={"quote": ALPHA_TEXT}).status_code == 404


def test_table_quote_highlights_each_matching_piece():
    buffer = BytesIO()
    page_width, page_height = 612, 792
    pdf = Canvas(buffer, pagesize=(page_width, page_height))
    matching_cells = [
        (45, 735, "Director roles"),
        (285, 735, "8 weeks"),
        (45, 660, "Business days"),
        (45, 585, "Return equipment"),
    ]
    short_cell = (285, 585, "HR")
    for cell in [*matching_cells, short_cell]:
        pdf.drawString(*cell)
    pdf.save()
    data = buffer.getvalue()
    quote = "Director roles | 8 weeks … Business days ... Return equipment | Absent column | HR"

    with Image.open(BytesIO(render_preview(data, 1))) as plain, Image.open(BytesIO(render_preview(data, 1, quote))) as marked:
        changes = ImageChops.difference(plain.convert("RGB"), marked.convert("RGB"))

    def cell_changed(x, y, _text):
        scale_x, scale_y = changes.width / page_width, changes.height / page_height
        return changes.crop((
            int((x - 4) * scale_x), int((page_height - y - 18) * scale_y),
            int((x + 130) * scale_x), int((page_height - y + 4) * scale_y),
        )).getbbox() is not None

    assert all(cell_changed(*cell) for cell in matching_cells)
    assert not cell_changed(*short_cell)
