"""Run the Sourced handbook retrieval comparison on disposable Docker Postgres."""

from statistics import mean
from unittest.mock import patch

from sqlalchemy.orm import sessionmaker

from backend.app.chat import SAMPLE_PDF
from backend.app.db.models import Workspace
from backend.app.documents import enqueue_document
from backend.app.settings import DocumentLimits
from backend.app.store import PgVectorStore
from backend.app.worker import process_one
from rag.documents import chunk_pages, count_pages, load_pdf
from rag.offline import HashingEmbedder
from rag.store import VectorStore

from .postgres import disposable_postgres


def compare(sessions: sessionmaker):
    with patch("dotenv.load_dotenv"):
        from eval.run_eval import gold_pages, load_cases, page_metrics

    pdf = SAMPLE_PDF.read_bytes()
    embedder = HashingEmbedder()
    with sessions.begin() as db:
        workspace = Workspace(name="Retrieval evaluation")
        db.add(workspace)
        db.flush()
        workspace_id = workspace.id
        enqueue_document(db, workspace_id, SAMPLE_PDF.name, pdf, count_pages(pdf))
    assert process_one(sessions, DocumentLimits(10 * 1024**2, 80, 500_000, 20), embedder)

    memory = VectorStore(embedder, "m2-evaluation")
    try:
        memory.add(chunk_pages(load_pdf(pdf, SAMPLE_PDF.name)))
        pg = PgVectorStore(sessions, embedder, workspace_id)
        cases = [case for case in load_cases(None) if gold_pages(case) and not case.get("history")]
        print(f"Sourced hybrid retrieval at k=5 on {len(cases)} handbook questions")

        def score(store):
            results = [
                page_metrics(
                    gold_pages(case),
                    [hit.chunk.page for hit in store.search(case["question"], 5, mode="hybrid")],
                    case.get("needs_all_pages", False),
                )
                for case in cases
            ]
            hits, reciprocal_ranks = zip(*results)
            return mean(hits), mean(reciprocal_ranks)

        baseline, result = score(memory), score(pg)
        print(f"In-memory  Hit@5 {baseline[0]:.2%}  MRR {baseline[1]:.4f}")
        print(f"Postgres   Hit@5 {result[0]:.2%}  MRR {result[1]:.4f}")
        assert baseline[0] >= 0.90
        assert abs(baseline[0] - result[0]) <= 0.04, "Hit@5 drift exceeds one question."
        assert abs(baseline[1] - result[1]) <= 0.03, "MRR drift exceeds 0.03."
    finally:
        memory.drop()


if __name__ == "__main__":
    with disposable_postgres() as engine:
        compare(sessionmaker(engine))
