"""Request and response bodies for the Sourced API."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
HistoryText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=600)]
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]


class HistoryMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: HistoryText


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: Question
    history: list[HistoryMessage] = Field(default_factory=list, max_length=6)
    provider: Identifier | None = None
    document_ids: list[Identifier] | None = Field(default=None, max_length=1)


class WorkspaceChatRequest(ChatRequest):
    document_ids: list[UUID] | None = Field(default=None, max_length=100)


class ProviderStatus(BaseModel):
    name: str
    label: str
    ready: bool


class CitationResponse(BaseModel):
    number: int
    document_id: str
    source: str
    page: int
    quote: str
    verified: bool
    preview_url: str | None = None


class PassageResponse(BaseModel):
    document_id: str
    source: str
    page: int
    text: str


class JudgementResponse(BaseModel):
    verdict: Literal["supported", "partial", "unsupported", "error"]
    problems: list[str]
    judge: str


class ChatResponse(BaseModel):
    status: Literal["answered", "not_found", "unverified", "invalid_output"]
    text: str
    partial: bool = False
    searched_for: str = ""
    reason: str = ""
    fallback: str = ""
    citations: list[CitationResponse] = Field(default_factory=list)
    passages: list[PassageResponse] = Field(default_factory=list)
    judgement: JudgementResponse | None = None
