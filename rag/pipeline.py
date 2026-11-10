"""The question-answering pipeline: rewrite follow-ups, retrieve, answer with citations."""
from __future__ import annotations

from .citations import INVALID_OUTPUT, NOT_FOUND, Answer, build_answer
from .providers import ChatModel
from .store import VectorStore

ANSWER_SYSTEM = """You answer questions using ONLY the numbered sources provided. They are excerpts from the user's documents.

Rules:
- Use only facts stated in the sources. Never use outside knowledge, and never guess.
- If the sources do not contain the answer, set "found" to false and say briefly that the documents don't cover it. Do not add citations in that case.
- If the sources answer only part of the question, answer that part and say what is not covered.
- If the sources give a rule or method instead of an exact figure (for example "pro-rated based on contracted hours"), that rule is the answer: state it, cite it, and set "found" to true.
- After each fact, add a marker like [1] or [2] for the source it came from. Every sentence that contains a number needs a marker.
- Copy numbers exactly as the source states them.
- For every source you cite, copy one short quote (one sentence or less) word for word from that source, exactly as written.
- Keep the answer short and direct: one to four sentences, or a short bullet list for several items. Plain language.
- Answer in the same language as the question.

Reply with JSON only, no other text, in this shape:
{"found": true, "answer": "Full-time employees get 24 days of annual leave per year [1].", "citations": [{"source": 1, "quote": "Full-time employees get 24 days of paid annual leave each calendar year."}]}"""

CONDENSE_SYSTEM = """Rewrite the user's latest question so it can be understood without the conversation, \
for searching a document. Resolve words like "it", "that" or "they" using the conversation. \
If the question already stands on its own, return it unchanged. Reply with the rewritten question only."""

NOT_FOUND_TEXT = "I couldn't find that in the documents."

REPAIR_PROMPT = """Your previous reply was not valid. Reply again with JSON only, exactly in the required shape: \
{"found": true or false, "answer": "...", "citations": [{"source": <number>, "quote": "..."}]}"""


def format_sources(hits) -> str:
    blocks = []
    for i, hit in enumerate(hits, 1):
        blocks.append(f"[{i}] (file: {hit.chunk.source}, page {hit.chunk.page})\n{hit.chunk.text}")
    return "\n\n".join(blocks)


class RAGPipeline:
    def __init__(self, store: VectorStore, chat: ChatModel, top_k: int = 5, min_score: float = 0.0):
        self.store = store
        self.chat = chat
        self.top_k = top_k
        self.min_score = min_score

    def standalone_question(self, question: str, history: list[dict]) -> str:
        """Turn a follow-up like "what about part-time staff?" into a full question for retrieval."""
        if not history:
            return question
        recent = history[-6:]
        convo = "\n".join(f"{m['role'].upper()}: {m['content'][:600]}" for m in recent)
        prompt = f"Conversation:\n{convo}\n\nLatest question: {question}"
        try:
            rewritten = self.chat.complete(CONDENSE_SYSTEM, [{"role": "user", "content": prompt}], max_tokens=120)
        except Exception:
            return question
        rewritten = rewritten.strip().strip('"').strip()
        return rewritten if 3 < len(rewritten) < 500 else question

    def ask(self, question: str, history: list[dict] | None = None) -> Answer:
        query = self.standalone_question(question, history or [])
        hits = [h for h in self.store.search(query, self.top_k) if h.score >= self.min_score]
        if not hits:
            return Answer(text=NOT_FOUND_TEXT, found=False, hits=[], search_query=query, status=NOT_FOUND)

        user = f"Sources:\n\n{format_sources(hits)}\n\nQuestion: {query}"
        raw = self.chat.complete(ANSWER_SYSTEM, [{"role": "user", "content": user}], max_tokens=900)
        answer = build_answer(raw, hits, query)
        if answer.status == INVALID_OUTPUT:
            # One retry for a malformed reply. Evidence failures are not retried.
            messages = [{"role": "user", "content": user}]
            if raw.strip():
                messages += [{"role": "assistant", "content": raw[:4000]}, {"role": "user", "content": REPAIR_PROMPT}]
            raw = self.chat.complete(ANSWER_SYSTEM, messages, max_tokens=900)
            answer = build_answer(raw, hits, query)
            answer.attempts = 2
        if not answer.found and not answer.text:
            answer.text = NOT_FOUND_TEXT
        return answer
