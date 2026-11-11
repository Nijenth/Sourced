"""Answer questions against the bundled handbook with the Sourced engine."""

import re
from pathlib import Path
from threading import Lock

from rag.citations import ANSWERED, BLOCKED_TEXT, INVALID_OUTPUT, UNVERIFIED, Answer, snippet
from rag.config import Settings
from rag.documents import chunk_pages, load_pdf
from rag.judge import Judge
from rag.pipeline import NOT_FOUND_TEXT, RAGPipeline
from rag.providers import make_chat, make_embedder
from rag.rerank import Reranker, make_reranker
from rag.store import VectorStore

from .schemas import ChatResponse, CitationResponse, JudgementResponse, PassageResponse

SAMPLE_PDF = Path(__file__).resolve().parents[2] / "data" / "sample" / "Brightleaf_Studio_Employee_Handbook.pdf"
SAMPLE_DOCUMENT_ID = "brightleaf-handbook"
BLOCK_REASONS = {
    UNVERIFIED: "The answer didn't pass the source checks.",
    INVALID_OUTPUT: "The model didn't return an answer in the required format.",
}


class SampleChat:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._store: VectorStore | None = None
        self._reranker: Reranker | None = None
        self._index_lock = Lock()

    def _get_store(self) -> VectorStore:
        with self._index_lock:
            if self._store is None:
                embedder = make_embedder()
                reranker = None
                if not self.settings.fake_mode:
                    reranker = make_reranker(self.settings.reranker, self.settings.rerank_model)
                chunks = chunk_pages(load_pdf(SAMPLE_PDF.read_bytes(), SAMPLE_PDF.name))
                store = VectorStore(embedder, "sourced-api-sample")
                try:
                    store.add(chunks)
                except Exception:
                    store.drop()
                    raise
                self._store, self._reranker = store, reranker
            return self._store

    def ask(self, question: str, history: list[dict], provider: str) -> ChatResponse:
        chat = make_chat(provider)
        judge = None
        if self.settings.judge and not self.settings.fake_mode:
            judge = Judge(make_chat(self.settings.judge_provider) if self.settings.judge_provider else chat)
        pipeline = RAGPipeline(
            self._get_store(),
            chat,
            top_k=self.settings.top_k,
            retrieval_mode=self.settings.retrieval_mode,
            reranker=self._reranker,
            rerank_pool=self.settings.rerank_pool,
            judge=judge,
            judge_action=self.settings.judge_action,
        )
        return to_response(pipeline.ask(question, history), question)

    def close(self) -> None:
        if self._store is not None:
            self._store.drop()


def _normalise_question(question: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", question.lower()).split())


def to_response(answer: Answer, question: str) -> ChatResponse:
    searched_for = ""
    if answer.search_query and _normalise_question(answer.search_query) != _normalise_question(question):
        searched_for = answer.search_query

    answered = answer.status == ANSWERED
    blocked = answer.status in BLOCK_REASONS
    text = NOT_FOUND_TEXT
    if answered:
        text = answer.text
    elif blocked:
        text = BLOCKED_TEXT
    judgement = None
    if answer.judgement is not None:
        # A judge can quote the original answer in its findings.
        problems = answer.judgement.problems if answered else []
        if answer.judgement.verdict == "error":
            problems = ["The accuracy check could not complete."]
        judgement = JudgementResponse(verdict=answer.judgement.verdict, problems=problems, judge=answer.judgement.judge)

    return ChatResponse(
        status=answer.status,
        text=text,
        partial=answered and answer.partial,
        searched_for=searched_for,
        reason=BLOCK_REASONS.get(answer.status, ""),
        citations=[
            CitationResponse(
                number=citation.number,
                document_id=SAMPLE_DOCUMENT_ID,
                source=citation.source,
                page=citation.page,
                quote=citation.quote,
                verified=citation.verified,
            )
            for citation in answer.citations
        ] if answered else [],
        passages=[
            PassageResponse(
                document_id=SAMPLE_DOCUMENT_ID,
                source=hit.chunk.source,
                page=hit.chunk.page,
                text=snippet(hit.chunk.text),
            )
            for hit in answer.hits[:3]
        ] if blocked else [],
        judgement=judgement,
    )
