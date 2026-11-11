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
from .db.models import UsageDaily, Widget
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
    channel_label: str | None = None


def record_answer(db: Db, workspace_id: UUID, answer: ChatResponse, channel: str = "ask_test") -> ChatResponse:
    counts = {
        "questions": 1,
        "answered": int(answer.status == "answered"),
        "not_found": int(answer.status == "not_found"),
        "blocked": int(answer.status in {"unverified", "invalid_output"}),
    }
    db.execute(
        insert(UsageDaily)
        .values(workspace_id=workspace_id, channel=channel, day=datetime.now(timezone.utc).date(), **counts)
        .on_conflict_do_update(
            index_elements=[UsageDaily.workspace_id, UsageDaily.day, UsageDaily.channel],
            set_={name: getattr(UsageDaily, name) + increment for name, increment in counts.items()},
        )
    )
    db.commit()
    return answer


@router.get("/usage", response_model_exclude_none=True)
def usage(db: Db, member: CurrentMember, days: Annotated[int, Query(ge=1, le=90)] = 30) -> list[UsageRow]:
    today = datetime.now(timezone.utc).date()
    rows = db.scalars(
        select(UsageDaily)
        .where(UsageDaily.workspace_id == member.workspace_id, UsageDaily.day.between(today - timedelta(days=days - 1), today))
        .order_by(UsageDaily.day.desc(), UsageDaily.channel)
    )
    result = [UsageRow.model_validate(row) for row in rows]
    if any(row.channel.startswith("widget:") for row in result):
        names = {f"widget:{id}": name for id, name in db.execute(
            select(Widget.id, Widget.name).where(Widget.workspace_id == member.workspace_id)
        )}
        for row in result:
            if row.channel.startswith("widget:"):
                row.channel_label = names.get(row.channel, "Deleted widget")
    return result
