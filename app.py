"""DocChat: chat with your PDFs, with the page and exact passage behind every answer.

Run locally:  streamlit run app.py
"""
from __future__ import annotations

import datetime as dt
import io
import os
import re
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

from rag.citations import INVALID_OUTPUT, UNVERIFIED, snippet  # noqa: E402
from rag.config import Settings, load_settings  # noqa: E402
from rag.documents import PdfError, chunk_pages, count_pages, file_hash, load_pdf  # noqa: E402
from rag.pipeline import RAGPipeline  # noqa: E402
from rag.incident import format_incident_draft, policy_note  # noqa: E402
from rag.judge import Judge  # noqa: E402
from rag.rerank import make_reranker  # noqa: E402
from rag.providers import (  # noqa: E402
    PROVIDERS,
    ProviderError,
    chat_provider_names,
    embedding_provider_name,
    make_chat,
    make_embedder,
    setup_problems,
)
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
def get_embedder(provider: str | None, model: str | None):
    # The arguments are the cache key: changing the provider or model builds a new embedder.
    return make_embedder()


def get_chat(provider: str):
    return make_chat(provider)


@st.cache_resource(show_spinner="Loading the reranker…")
def get_reranker(name: str, model: str):
    return make_reranker(name, model)


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
    st.caption(policy_note())
    with st.form("incident_report"):
        device = st.text_input("Affected device", placeholder="Laptop or phone", max_chars=100)
        when = st.text_input("When did you notice?", max_chars=120)
        description = st.text_area("What happened?", max_chars=2000)
        submitted = st.form_submit_button("Create draft", type="primary")

    if submitted:
        draft = format_incident_draft(device, when, description)
        if draft is None:
            st.warning("Describe what happened to create a draft.")
            return
        st.code(draft, language=None, wrap_lines=True)


def render_answer(msg: dict, idx: int, files: dict[str, bytes]) -> None:
    st.markdown(md_escape(msg["content"]))
    if msg.get("error"):
        return
    if msg.get("status") in ("unverified", "invalid_output"):
        if msg.get("reason"):
            st.caption(f"Why: {msg['reason']}")
        for i, p in enumerate(msg.get("passages", []), 1):
            with st.expander(f"{p['source']} · page {p['page']}"):
                st.markdown("> " + md_escape(p["text"]))
                data = files.get(p["source"])
                if data and st.toggle("Show the page", key=f"passage-{idx}-{i}"):
                    st.image(render_page(file_hash(data), p["page"], data), width="stretch")
        return
    if not msg.get("found"):
        checked = sorted({(h["source"], h["page"]) for h in msg.get("checked", [])}, key=lambda x: (x[0], x[1]))
        if checked:
            pages = ", ".join(f"p. {p}" for _, p in checked[:6])
            st.caption(f"Searched the closest passages ({pages}) and none of them answer this.")
        return

    if msg.get("searched_for"):
        st.caption(f"Searched for: “{md_escape(msg['searched_for'])}”")
    judgement = msg.get("judgement")
    if judgement:
        problems = "; ".join(md_escape(p) for p in judgement.get("problems", [])[:3])
        verdict = judgement.get("verdict")
        by = f" ({md_escape(judgement['judge'])})" if judgement.get("judge") else ""
        if verdict == "supported":
            st.caption(f"✓ Accuracy check{by}: every claim matches the passages.")
        elif verdict == "partial":
            st.warning(f"Accuracy check{by}: some details may not be fully supported by the sources." + (f" ({problems})" if problems else ""))
        elif verdict == "unsupported":
            st.error(f"Accuracy check{by}: the passages don't support this answer." + (f" ({problems})" if problems else ""))
        else:
            st.caption(f"The accuracy check{by} couldn't run for this answer." + (f" {problems}" if problems else ""))
    if msg.get("partial"):
        st.caption("The documents only partly answer this. Check the sources below.")
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


def upload_notice() -> str:
    """Say plainly where uploaded text goes: the embedding provider indexes it, the chosen model reads passages."""
    name = embedding_provider_name()
    if name in ("ollama", "offline"):
        where = "Indexed on this computer"
    else:
        where = f"Indexed by {PROVIDERS[name].label if name in PROVIDERS else name}"
    return (f"Kept for this session only, never saved. {where}; relevant passages go to the model below "
            "(Ollama keeps everything local). Don't upload confidential files to a public demo.")


def _same_question(a: str, b: str) -> bool:
    simple = lambda s: " ".join(re.sub(r"[^\w\s]", " ", s.lower()).split())  # noqa: E731
    return simple(a) == simple(b)


def answer_to_message(answer, question: str = "") -> dict:
    return {
        # Follow-ups are rewritten before searching; show the rewrite so it is never a silent change.
        "searched_for": answer.search_query
        if question and answer.search_query and not _same_question(question, answer.search_query)
        else "",
        "role": "assistant",
        "content": answer.text,
        "found": answer.found,
        "partial": answer.partial,
        "judgement": {"verdict": answer.judgement.verdict, "problems": answer.judgement.problems,
                      "judge": answer.judgement.judge}
        if answer.judgement
        else None,
        "status": answer.status,
        "reason": answer.reason,
        "passages": [
            {"source": h.chunk.source, "page": h.chunk.page, "text": snippet(h.chunk.text)} for h in answer.hits[:3]
        ]
        if answer.status in (UNVERIFIED, INVALID_OUTPUT)
        else [],
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

problems = setup_problems()
if problems:
    st.error("This app isn't set up yet.\n\n" + "\n\n".join(f"- {p}" for p in problems))
    st.markdown(
        "Copy `.env.example` to `.env` and add your keys, or on Streamlit Community Cloud add them under "
        "**App settings › Secrets**. See the README for details."
    )
    st.stop()

try:
    embedder = get_embedder(embedding_provider_name(), os.getenv("EMBEDDING_MODEL"))
except ProviderError as exc:
    st.error(str(exc))
    st.stop()
ss = st.session_state
ss.setdefault("messages", {MODE_SAMPLE: [], MODE_UPLOAD: []})
ss.setdefault("asked", 0)
ss.setdefault("upload", {"key": None, "store": None, "files": {}, "warnings": []})


# ---------- sidebar -----------------------------------------------------------------------

with st.sidebar:
    st.subheader("Documents")
    if settings.allow_uploads:
        mode = st.radio("Knowledge base", [MODE_SAMPLE, MODE_UPLOAD], label_visibility="collapsed")
    else:
        mode = MODE_SAMPLE  # public demo: fictional handbook only

    store: VectorStore | None = None
    files: dict[str, bytes] = {}

    if mode == MODE_SAMPLE:
        st.caption(
            "A 16-page employee handbook for Brightleaf Studio, a fictional design agency. "
            "Ask about leave, remote work, expenses, equipment and more."
        )
        st.download_button("Download the handbook (PDF)", SAMPLE_PDF.read_bytes(), SAMPLE_PDF.name, "application/pdf")
        if not settings.allow_uploads:
            st.caption("This public demo uses the fictional handbook only. Run DocChat yourself to use your own PDFs.")
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
        st.caption(upload_notice())

    st.subheader("Model")
    providers = chat_provider_names()
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
        try:
            # Offline test mode never downloads anything, so it skips reranking.
            reranker = None if settings.fake_mode else get_reranker(settings.reranker, settings.rerank_model)
        except ProviderError as exc:
            st.warning(f"{exc} Answering without reranking.")
            reranker = None
        judge = None
        if settings.judge and not settings.fake_mode:
            try:
                judge = Judge(get_chat(settings.judge_provider or provider))
            except ProviderError as exc:
                st.warning(f"{exc} Answering without the accuracy check.")
        pipeline = RAGPipeline(store, get_chat(provider), top_k=settings.top_k, retrieval_mode=settings.retrieval_mode,
                               reranker=reranker, rerank_pool=settings.rerank_pool, judge=judge,
                               judge_action=settings.judge_action)
        with st.chat_message("assistant"):
            with st.spinner("Searching the documents…"):
                try:
                    reply = answer_to_message(pipeline.ask(prompt, history), prompt)
                    ss.asked += 1
                except ProviderError as exc:
                    hint = " You can pick another model in the sidebar." if len(providers) > 1 else ""
                    reply = {"role": "assistant", "content": str(exc) + hint, "error": True}
    messages.append({"role": "user", "content": prompt})
    messages.append(reply)
    st.rerun()
