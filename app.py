"""DocChat: chat with your PDFs, with the page and exact passage behind every answer.

Run locally:  streamlit run app.py
"""
from __future__ import annotations

import datetime as dt
import io
import os
import threading
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

load_dotenv()
try:  # On Streamlit Community Cloud, keys live in st.secrets. Copy them into the environment.
    for _k, _v in st.secrets.items():
        if isinstance(_v, (str, int, float, bool)):
            os.environ.setdefault(_k, str(_v))
except Exception:
    pass

from rag.config import Settings, load_settings  # noqa: E402
from rag.documents import PdfError, chunk_pages, count_pages, file_hash, load_pdf  # noqa: E402
from rag.pipeline import RAGPipeline  # noqa: E402
from rag.providers import ClaudeChat, OpenAIChat, OpenAIEmbedder, ProviderError  # noqa: E402
from rag.store import VectorStore  # noqa: E402

ROOT = Path(__file__).parent
SAMPLE_PDF = ROOT / "data" / "sample" / "Brightleaf_Studio_Employee_Handbook.pdf"
SAMPLE_QUESTIONS = [
    "How many days of annual leave do I get?",
    "Can I work from another country for a few weeks?",
    "What's the hotel limit when I travel for work?",
    "What happens if I lose my laptop?",
    "Does Brightleaf offer stock options?",
    "What is the dress code?",
]
MODE_SAMPLE = "Sample HR handbook"
MODE_UPLOAD = "Upload your own PDFs"

st.set_page_config(page_title="DocChat: chat with your documents", page_icon="📄", layout="centered")
st.markdown(
    """
    <style>
      .block-container {padding-top: 2.2rem; max-width: 820px;}
      div[data-testid="stExpander"] summary p {font-size: 0.9rem;}
      .dc-sub {color: #5f6b66; margin-top: -0.6rem; margin-bottom: 1.2rem;}
    </style>
    """,
    unsafe_allow_html=True,
)

settings: Settings = load_settings()


# ---------- cached resources ----------------------------------------------------------------

@st.cache_resource(show_spinner=False)
def get_embedder(fake: bool, key: str | None, model: str):
    if fake:
        from rag.offline import HashingEmbedder

        return HashingEmbedder()
    return OpenAIEmbedder(api_key=key, model=model)


def get_chat(provider: str):
    if provider == "offline":
        from rag.offline import KeywordChat

        return KeywordChat()
    if provider == "claude":
        return ClaudeChat(settings.anthropic_api_key, settings.claude_model)
    return OpenAIChat(settings.openai_api_key, settings.openai_model)


@st.cache_resource(show_spinner="Indexing the sample handbook…")
def sample_store(embedder_name: str, _embedder) -> VectorStore:
    data = SAMPLE_PDF.read_bytes()
    store = VectorStore(_embedder, "sample")
    store.add(chunk_pages(load_pdf(data, SAMPLE_PDF.name)))
    return store


@st.cache_resource
def upload_registry() -> dict:
    """Upload stores across all sessions, so abandoned ones can be freed after a while."""
    return {"stores": [], "lock": threading.Lock()}


def register_upload(store: VectorStore, max_age_s: int = 2 * 3600) -> None:
    reg, now = upload_registry(), dt.datetime.now().timestamp()
    with reg["lock"]:
        for _, old in [x for x in reg["stores"] if now - x[0] > max_age_s]:
            old.drop()
        reg["stores"] = [x for x in reg["stores"] if now - x[0] <= max_age_s] + [(now, store)]


@st.cache_resource
def daily_usage() -> dict:
    return {"date": None, "count": 0, "lock": threading.Lock()}


@st.cache_data(show_spinner=False, max_entries=64)
def render_page(doc_hash: str, page: int, _data: bytes) -> bytes:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(_data)
    try:
        image = pdf[page - 1].render(scale=1.6).to_pil()
    finally:
        pdf.close()
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


# ---------- helpers -----------------------------------------------------------------------

def md_escape(text: str) -> str:
    """Stop Streamlit from reading dollar amounts as LaTeX math."""
    return text.replace("$", "\\$")


def take_daily_slot() -> bool:
    usage = daily_usage()
    with usage["lock"]:
        today = dt.date.today()
        if usage["date"] != today:
            usage["date"], usage["count"] = today, 0
        if usage["count"] >= settings.max_questions_per_day:
            return False
        usage["count"] += 1
        return True


def unique_name(name: str, taken: set[str]) -> str:
    if name not in taken:
        return name
    stem, dot, ext = name.rpartition(".")
    i = 2
    while f"{stem} ({i}).{ext}" in taken:
        i += 1
    return f"{stem} ({i}).{ext}"


def index_uploads(files, embedder) -> tuple[VectorStore | None, dict[str, bytes], list[str]]:
    """Validate and index uploaded PDFs. Returns the store, the files by name, and any warnings."""
    warnings: list[str] = []
    if len(files) > settings.max_upload_files:
        warnings.append(f"Only the first {settings.max_upload_files} files were used.")
        files = files[: settings.max_upload_files]

    accepted: dict[str, bytes] = {}
    total_pages = 0
    for f in files:
        data = f.getvalue()
        if len(data) > settings.max_upload_mb * 1024 * 1024:
            warnings.append(f"{f.name} is over {settings.max_upload_mb} MB and was skipped.")
            continue
        try:
            n = count_pages(data)
        except PdfError:
            warnings.append(f"{f.name} could not be read as a PDF and was skipped.")
            continue
        if total_pages + n > settings.max_upload_pages:
            warnings.append(f"{f.name} was skipped: the demo allows {settings.max_upload_pages} pages in total.")
            continue
        total_pages += n
        accepted[unique_name(f.name, set(accepted))] = data

    chunks = []
    for name, data in list(accepted.items()):
        try:
            chunks += chunk_pages(load_pdf(data, name))
        except PdfError as exc:
            warnings.append(str(exc))
            accepted.pop(name)
    if not chunks:
        return None, {}, warnings

    store = VectorStore(embedder, "upload")
    store.add(chunks)
    register_upload(store)
    return store, accepted, warnings


@st.dialog("Draft IT incident report")
def draft_incident_report() -> None:
    st.caption("Use fictional details in this demo. The draft is for you to review and copy.")
    st.caption(
        "Brightleaf’s sample handbook (page 12) says to report a lost or stolen laptop or phone "
        "to it@brightleaf.example within 24 hours."
    )
    with st.form("incident_report"):
        device = st.text_input("Affected device", placeholder="Laptop or phone", max_chars=100)
        when = st.text_input("When did you notice?", max_chars=120)
        description = st.text_area("What happened?", max_chars=2000)
        submitted = st.form_submit_button("Create draft", type="primary")

    if submitted:
        if not description.strip():
            st.warning("Describe what happened to create a draft.")
            return
        st.code(
            "To: it@brightleaf.example\n"
            "Subject: Lost or stolen device report\n\n"
            f"Affected device: {device.strip()}\n"
            f"When noticed: {when.strip()}\n\n"
            f"What happened:\n{description.strip()}",
            language=None,
            wrap_lines=True,
        )


def render_answer(msg: dict, idx: int, files: dict[str, bytes]) -> None:
    st.markdown(md_escape(msg["content"]))
    if msg.get("error"):
        return
    if not msg.get("found"):
        checked = sorted({(h["source"], h["page"]) for h in msg.get("checked", [])}, key=lambda x: (x[0], x[1]))
        if checked:
            pages = ", ".join(f"p. {p}" for _, p in checked[:6])
            st.caption(f"Searched the closest passages ({pages}) and none of them answer this.")
        return

    citations = msg.get("citations", [])
    if not citations:
        return
    st.caption("Sources")
    for c in citations:
        with st.expander(f"[{c['number']}]  {c['source']} · page {c['page']}"):
            st.markdown("> " + md_escape(c["quote"]))
            if not c["verified"]:
                st.caption("Closest passage on this page. The model's quote didn't match the text word for word.")
            data = files.get(c["source"])
            if data and st.toggle("Show the page", key=f"page-{idx}-{c['number']}"):
                st.image(render_page(file_hash(data), c["page"], data), width="stretch")


def answer_to_message(answer) -> dict:
    return {
        "role": "assistant",
        "content": answer.text,
        "found": answer.found,
        "citations": [
            {"number": c.number, "source": c.source, "page": c.page, "quote": c.quote, "verified": c.verified}
            for c in answer.citations
        ],
        "checked": [{"source": h.chunk.source, "page": h.chunk.page} for h in answer.hits],
    }


# ---------- setup checks ------------------------------------------------------------------

st.title("Chat with your documents")
st.markdown(
    '<p class="dc-sub">Ask a question and get an answer drawn only from the PDFs, '
    "with the page and exact passage behind every claim.</p>",
    unsafe_allow_html=True,
)

if settings.fake_mode:
    st.warning("Offline test mode: answers come from keyword matching, not AI. Unset RAG_OFFLINE_MODE for the real app.")

if not settings.can_embed or not settings.providers:
    missing = []
    if not settings.openai_api_key:
        missing.append("`OPENAI_API_KEY` (needed to index documents)")
    if not settings.providers:
        missing.append("`ANTHROPIC_API_KEY` or `OPENAI_API_KEY` (needed to answer)")
    st.error("This app needs API keys before it can run: " + "; ".join(missing) + ".")
    st.markdown(
        "Copy `.env.example` to `.env` and add your keys, or on Streamlit Community Cloud add them under "
        "**App settings › Secrets**. See the README for details."
    )
    st.stop()

embedder = get_embedder(settings.fake_mode, settings.openai_api_key, settings.embedding_model)
ss = st.session_state
ss.setdefault("messages", {MODE_SAMPLE: [], MODE_UPLOAD: []})
ss.setdefault("asked", 0)
ss.setdefault("upload", {"key": None, "store": None, "files": {}, "warnings": []})


# ---------- sidebar -----------------------------------------------------------------------

with st.sidebar:
    st.subheader("Documents")
    mode = st.radio("Knowledge base", [MODE_SAMPLE, MODE_UPLOAD], label_visibility="collapsed")

    store: VectorStore | None = None
    files: dict[str, bytes] = {}

    if mode == MODE_SAMPLE:
        st.caption(
            "A 16-page employee handbook for Brightleaf Studio, a fictional design agency. "
            "Ask about leave, remote work, expenses, equipment and more."
        )
        st.download_button("Download the handbook (PDF)", SAMPLE_PDF.read_bytes(), SAMPLE_PDF.name, "application/pdf")
        try:
            store = sample_store(embedder.name, embedder)
        except ProviderError as exc:
            st.error(str(exc))
            st.stop()
        files = {SAMPLE_PDF.name: SAMPLE_PDF.read_bytes()}
    else:
        uploaded = st.file_uploader(
            f"Up to {settings.max_upload_files} PDFs, {settings.max_upload_mb} MB each",
            type=["pdf"],
            accept_multiple_files=True,
        )
        key = tuple(sorted((f.name, file_hash(f.getvalue())) for f in uploaded)) if uploaded else None
        if key != ss.upload["key"]:
            if ss.upload["store"] is not None:
                ss.upload["store"].drop()
            ss.upload = {"key": key, "store": None, "files": {}, "warnings": []}
            ss.messages[MODE_UPLOAD] = []
            if uploaded:
                with st.spinner("Reading and indexing your PDFs…"):
                    try:
                        s, fs, warns = index_uploads(uploaded, embedder)
                    except ProviderError as exc:
                        s, fs, warns = None, {}, [str(exc)]
                ss.upload.update(store=s, files=fs, warnings=warns)
        for w in ss.upload["warnings"]:
            st.warning(w)
        store, files = ss.upload["store"], ss.upload["files"]
        if store is not None:
            st.success(f"Indexed {len(files)} file(s), {sum(store.sources.values())} pages.")
        st.caption("Uploads are kept in memory for this session only and are never stored.")

    st.subheader("Model")
    providers = settings.providers
    labels = {p: get_chat(p).label for p in providers}
    provider = st.selectbox("Answer with", providers, format_func=labels.get, label_visibility="collapsed")

    st.divider()
    left = max(0, settings.max_questions_per_session - ss.asked)
    st.caption(f"{left} of {settings.max_questions_per_session} questions left in this session.")
    if st.button("Clear chat", width="stretch"):
        ss.messages[mode] = []
        st.rerun()
    with st.expander("How it works"):
        st.markdown(
            "1. Each PDF is split into short passages, page by page.\n"
            "2. Passages are turned into embeddings and stored in a vector database (Chroma).\n"
            "3. Your question finds the closest passages.\n"
            "4. The model answers from those passages only and quotes them.\n"
            "5. Every quote is checked against the PDF text before it's shown."
        )
    if settings.repo_url:
        st.markdown(f"[Source code on GitHub]({settings.repo_url})")


# ---------- chat --------------------------------------------------------------------------

messages = ss.messages[mode]

if store is None:
    st.info("Upload one or more PDFs in the sidebar to start.")
    st.stop()

if not messages and mode == MODE_SAMPLE:
    st.markdown("**Try a question**")
    cols = st.columns(2)
    for i, q in enumerate(SAMPLE_QUESTIONS):
        if cols[i % 2].button(q, key=f"suggest-{i}", width="stretch"):
            ss.pending = q
            st.rerun()
elif not messages:
    names = ", ".join(files)
    st.markdown(f"Ready. Ask anything about **{md_escape(names)}**.")

if mode == MODE_SAMPLE and st.button("Draft IT incident report"):
    draft_incident_report()

for i, msg in enumerate(messages):
    with st.chat_message(msg["role"]):
        if msg["role"] == "user":
            st.markdown(md_escape(msg["content"]))
        else:
            render_answer(msg, i, files)

prompt = st.chat_input("Ask about the documents…")
if ss.get("pending"):
    prompt, ss.pending = ss.pending, None
prompt = (prompt or "").strip()[:1000]

if prompt:
    with st.chat_message("user"):
        st.markdown(md_escape(prompt))

    if ss.asked >= settings.max_questions_per_session:
        reply = {"role": "assistant", "content": "You've reached the question limit for this demo session.", "error": True}
    elif not take_daily_slot():
        reply = {"role": "assistant", "content": "The demo has reached today's question limit. Please try again tomorrow.", "error": True}
    else:
        history = [{"role": m["role"], "content": m["content"]} for m in messages if not m.get("error")]
        pipeline = RAGPipeline(store, get_chat(provider), top_k=settings.top_k)
        with st.chat_message("assistant"):
            with st.spinner("Searching the documents…"):
                try:
                    reply = answer_to_message(pipeline.ask(prompt, history))
                    ss.asked += 1
                except ProviderError as exc:
                    reply = {"role": "assistant", "content": str(exc), "error": True}
    messages.append({"role": "user", "content": prompt})
    messages.append(reply)
    st.rerun()
