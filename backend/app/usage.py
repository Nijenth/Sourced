"""Aggregate completed questions without storing the conversation."""

from datetime import date, datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from .auth import CurrentMember
from .db import Db
from .db.models import UsageDaily
from .schemas import ChatResponse

router = APIRouter(tags=["usage"])


class UsageRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    day: date
    channel: str
    questions: int
    answered: int
    not_found: int
    blocked: int


def record_answer(db: Db, workspace_id: UUID, answer: ChatResponse) -> ChatResponse:
    counts = {
        "questions": 1,
        "answered": int(answer.status == "answered"),
        "not_found": int(answer.status == "not_found"),
        "blocked": int(answer.status in {"unverified", "invalid_output"}),
    }
    db.execute(
        insert(UsageDaily)
        .values(workspace_id=workspace_id, channel="ask_test", day=datetime.now(timezone.utc).date(), **counts)
        .on_conflict_do_update(
            index_elements=[UsageDaily.workspace_id, UsageDaily.day, UsageDaily.channel],
            set_={name: getattr(UsageDaily, name) + increment for name, increment in counts.items()},
        )
    )
    db.commit()
    return answer


@router.get("/usage")
def usage(db: Db, member: CurrentMember, days: Annotated[int, Query(ge=1, le=90)] = 30) -> list[UsageRow]:
    today = datetime.now(timezone.utc).date()
    rows = db.scalars(
        select(UsageDaily)
        .where(UsageDaily.workspace_id == member.workspace_id, UsageDaily.day.between(today - timedelta(days=days - 1), today))
        .order_by(UsageDaily.day.desc(), UsageDaily.channel)
    )
    return [UsageRow.model_validate(row) for row in rows]
