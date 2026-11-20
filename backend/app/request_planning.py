"""Date semantics and reviewable schedule drafts; never write a plan from chat."""

import re
from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, Field

from backend.app.db.job_search import KST


class ScheduleFields(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    plan_type: Literal["cert_registration", "portfolio", "coding_test", "application", "other"] | None = None
    due_date: date | None = None
    start_date: date | None = None
    start_time: time | None = None
    end_date: date | None = None
    end_time: time | None = None
    notes: str | None = Field(default=None, max_length=3000)


def reference_from_text(request: str, today: date) -> tuple[bool, str | None]:
    # Protect explicit as-of dates even if a model mistakes one for a deadline.
    pattern = r"(\d{4})\s*(?:년\s*|[-./])(\d{1,2})\s*(?:월\s*|[-./])(\d{1,2})\s*(?:일\s*)?(?:기준|당시|시점|에\s*진행)"
    match = re.search(pattern, request)
    if match:
        prefix = request[:match.start()]
        if re.search(r"(?:마감|접수)(?:일|날짜|기한)?(?:은|이|을)?\s*$", prefix) and not re.search(r"당시|시점|이었던", request):
            return False, None
        try:
            return True, date(*(int(v) for v in match.groups())).isoformat()
        except ValueError:
            return True, None
    if re.search(r"(?:오늘|현재|지금)\s*(?:기준|시점|당시)", request):
        return True, today.isoformat()
    if re.search(r"(?:지난달|작년|내년|다음달|어제|내일)\s*(?:기준|당시|시점)", request):
        return True, None
    return False, None


def make_schedule_draft(analysis: dict) -> tuple[dict | None, list[str]]:
    fields = ScheduleFields.model_validate(analysis.get("schedule") or {})
    missing = []
    if not fields.title:
        missing.append("계획 제목")
    start = end = None
    has_times = fields.start_time is not None or fields.end_time is not None
    if fields.start_date is not None or has_times:
        if fields.start_date is None:
            missing.append("일정 날짜")
        if fields.start_time is None:
            missing.append("시작 시각")
        if fields.end_time is None:
            missing.append("종료 시각")
        if not missing:
            start = datetime.combine(fields.start_date, fields.start_time).replace(tzinfo=KST)
            end = datetime.combine(fields.end_date or fields.start_date, fields.end_time).replace(tzinfo=KST)
            if end <= start:
                missing.append("시작보다 늦은 종료 일시(자정을 넘으면 종료 날짜도 지정)")
    elif fields.due_date is None:
        missing.append("마감 날짜 또는 일정 날짜·시작·종료 시각")
    if missing:
        return None, missing
    return {
        "title": fields.title, "plan_type": fields.plan_type or "other",
        "due_date": fields.due_date.isoformat() if fields.due_date else None,
        "start_at": start.isoformat() if start else None,
        "end_at": end.isoformat() if end else None,
        "job_posting_id": analysis.get("job_id"), "growth_plan_id": None,
        "status": "planned", "notes": fields.notes,
    }, []
