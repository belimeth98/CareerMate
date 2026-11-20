"""Synchronous event publishing. This module has no SQLite or LLM dependency."""

import json
import logging
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from uuid import uuid4
from backend.app.job_sources import JOB_SOURCES

TOOLS = {"search_jobs", "get_job_detail", "search_ncs_skills", "get_cert_schedule"}
STAGES = {"analyze", "plan", "execute", "evaluate", "replan", "assess_fit", "respond"}
EVENT_TYPES = {
    "run_started", "run_finished", "stage_started", "stage_finished",
    "intent_decided", "plan_created", "constraint_decided", "route_decided",
    "replan_requested", "tool_requested", "tool_finished", "fit_decided", "error",
}
ERROR_CODES = {
    "INVALID_INPUT", "TOOL_ERROR", "INVALID_TOOL_RESPONSE", "DATABASE_ERROR",
    "NO_RESULT", "MISSING_API_KEY", "UPSTREAM_TIMEOUT", "UPSTREAM_ERROR",
    "UPSTREAM_INVALID_RESPONSE", "FIT_ERROR", "AGENT_ERROR", "UPSTREAM_PARTIAL_FAILURE",
    "CONFIGURATION_ERROR", "DEDUP_REFERENCE_UNAVAILABLE",
}
REASONS = {
    "experience_followup_needed": "점수 안내 전에 관련 경험을 추가로 확인합니다.",
    "experience_review_completed": "추가 경험 확인을 마치고 준비 방향과 참고 결과를 안내합니다.",
    "experience_coaching": "제공된 사실로 경험을 정리하고 부족한 내용을 질문합니다.",
    "image_draft": "캡처에서 읽은 요건과 모집 기간을 검토용 초안으로 준비합니다.",
    "user_requirements_needed": "RSS 상세 자격은 미확인이므로 원문 확인 후 사용자 입력을 기다립니다.",
    "user_requirements_received": "사용자가 선택한 공고의 자격요건을 입력하고 원문 확인을 표시했습니다.",
    "user_requirements_checked": "사용자가 입력한 요건을 기준으로 참고 적합도를 계산했습니다. 원문과의 일치는 검증하지 않았습니다.",
    "unsupported_reference_date": "요청 기준일의 공고 진행 이력을 확인할 수 없어 조회와 충족 판정을 하지 않았습니다.",
    "schedule_requested": "사용자가 지정한 일정 조건을 추출했습니다.",
    "schedule_draft_ready": "입력한 날짜와 시각으로 저장 전 검토할 일정 초안을 구성했습니다.",
    "schedule_missing_input": "일정 초안에 필요한 날짜·시각 또는 제목이 부족해 추가 입력을 요청합니다.",
    "tool_request": "요청을 처리할 조회 도구가 필요합니다.",
    "follow_up": "후속 요청이므로 이전 조건과 변경 조건을 합쳤습니다.",
    "general_chat": "인사 또는 사용 안내이므로 도구가 필요하지 않습니다.",
    "insufficient_input": "조회에 필요한 추가 조건을 확인해야 합니다.",
    "plan_ready": "확인된 의도와 입력으로 실행 순서를 정했습니다.",
    "no_result": "검색 조건에 맞는 공고가 0건입니다.",
    "tool_error": "도구 실행 오류로 결과를 확인하지 못했습니다.",
    "deadline_not_requested": "마감 조건이 없어 마감 제약을 적용하지 않았습니다.",
    "deadline_checked": "조회된 마감일로 요청한 마감 조건을 판단했습니다.",
    "expand_candidates": "같은 조건의 후보 범위를 넓혀 마감 조건을 다시 확인합니다.",
    "fit_requested": "후보 공고와 프로필로 적합도를 확인합니다.",
    "ready_to_respond": "확보한 결과와 확인하지 못한 내용을 응답합니다.",
    "fit_checked": "확인된 공고 기준으로 현재 및 미래 적합도를 계산했습니다.",
    "stage_error": "실행 단계에서 오류가 발생했습니다.",
}
ARGUMENT_FIELDS = {
    "keyword", "region", "employment_type", "career_type", "ncs_category", "limit",
    "job_id", "ncs_code", "year", "qualification_type", "qualification_code",
    "ongoing_only", "page", "page_size", "refresh", "source",
}
INTEGER_FIELDS = {
    "seq", "replan_count", "missing_count", "candidate_count", "eligible_count",
    "unmet_count", "unknown_count", "count", "job_id", "before_limit", "after_limit",
    "current_score", "future_score", "assessed_weight",
    "duplicate_count",
}
CHOICES = {
    "stage": STAGES,
    "tool": TOOLS,
    "next_stage": STAGES,
    "status": {"ok", "no_result", "error", "completed", "failed"},
    "search_status": {"ok", "no_result", "error", "partial_error", "not_requested"},
    "deadline_status": {"met", "unmet", "unknown", "not_requested", "not_evaluated"},
    "current_eligibility": {"met", "unmet", "unknown"},
    "future_eligibility": {"met", "unmet", "unknown"},
    "error_code": ERROR_CODES,
    "data_source": {"external_api", "rss", "cache", "mixed"},
    "source": {"all", *JOB_SOURCES},
    "evidence_source": {"user_submitted"},
    "filter_status": {"verified", "unverified"},
    "availability_status": {"unknown"},
}


def safe_error_code(value: object) -> str:
    return value if isinstance(value, str) and value in ERROR_CODES else "TOOL_ERROR"


def _safe_payload(payload: dict) -> dict:
    """Allow facts only; never publish raw prompts, profiles, Tool bodies or errors.

    Even text in apparently harmless argument names (keyword, region, etc.) may
    contain credentials or personal data, so only their field names are retained.
    Reasons are public fixed descriptions selected by a decision code.
    """
    safe = {}
    for key, value in payload.items():
        if key in INTEGER_FIELDS and type(value) is int and value >= 0:
            safe[key] = value
        elif key in CHOICES and isinstance(value, str) and value in CHOICES[key]:
            safe[key] = value
        elif key in {"used_previous_context", "should_replan"} and type(value) is bool:
            safe[key] = value
        elif key == "call_id" and isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value):
            safe[key] = value
        elif key in {"intents", "tools", "argument_fields"} and isinstance(value, list):
            allowed = ARGUMENT_FIELDS if key == "argument_fields" else TOOLS
            safe[key] = [item for item in value if isinstance(item, str) and item in allowed]
        elif key == "source_results" and isinstance(value, list):
            safe[key] = [{"source": row["source"], "status": row["status"],
                          **({"error_code": safe_error_code(row["error_code"])} if "error_code" in row else {})}
                         for row in value[:4] if isinstance(row, dict) and row.get("source") in JOB_SOURCES
                         and row.get("status") in {"ok", "no_result", "error"}]
        elif key == "job_ids" and isinstance(value, list):
            safe[key] = [item for item in value[:100] if type(item) is int and item > 0]
        elif key == "reason_code" and isinstance(value, str) and value in REASONS:
            safe[key] = value
            safe["reason"] = REASONS[value]
        elif key == "arguments" and isinstance(value, dict):
            arguments = {}
            for name, argument in value.items():
                if name in {"limit", "job_id", "year", "page", "page_size"}:
                    if type(argument) is int and argument > 0:
                        arguments[name] = argument
                elif name in {"ongoing_only", "refresh"} and type(argument) is bool:
                    arguments[name] = argument
                elif name == "source" and isinstance(argument, str) and argument in {"all", *JOB_SOURCES}:
                    arguments[name] = argument
                # All free text and unrecognised fields are excluded before dispatch.
            safe[key] = arguments
    return safe


@dataclass(frozen=True)
class ExecutionEvent:
    session_id: str
    run_id: str
    event_type: str
    created_at: str
    duration_ms: int | None
    payload: str  # Immutable JSON: one subscriber cannot alter another's event.


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[Callable[[ExecutionEvent], None]] = []
        self._lock = RLock()
        self.failure_count = 0

    def subscribe(self, subscriber: Callable[[ExecutionEvent], None]) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(subscriber)

        def unsubscribe() -> None:
            with self._lock:
                if subscriber in self._subscribers:
                    self._subscribers.remove(subscriber)

        return unsubscribe

    def publish(self, event: ExecutionEvent) -> None:
        # Synchronous delivery preserves publication order for every subscriber.
        with self._lock:
            for subscriber in tuple(self._subscribers):
                try:
                    subscriber(event)
                except Exception:
                    self.failure_count += 1
                    if self.failure_count == 1:
                        try:
                            logging.getLogger(__name__).warning(
                                "실행 로그 구독 처리에 실패했습니다. 대화는 계속합니다."
                            )
                        except Exception:
                            pass


class EventPublisher:
    def __init__(self, bus: EventBus, session_id: str) -> None:
        self.bus = bus
        self.session_id = session_id
        self.run_id = uuid4().hex
        self._seq = 0
        self._lock = RLock()
        self.failure_count = 0

    def emit(self, event_type: str, *, duration_ms: int | None = None, **payload) -> None:
        # Event construction and privacy filtering must also fail open.
        try:
            if event_type not in EVENT_TYPES:
                return
            with self._lock:
                self._seq += 1
                safe = _safe_payload(payload)
                safe["seq"] = self._seq
                event = ExecutionEvent(
                    session_id=self.session_id,
                    run_id=self.run_id,
                    event_type=event_type,
                    created_at=datetime.now(timezone.utc).isoformat(timespec="microseconds"),
                    duration_ms=max(0, duration_ms) if type(duration_ms) is int else None,
                    payload=json.dumps(safe, ensure_ascii=False, separators=(",", ":")),
                )
                self.bus.publish(event)
        except Exception:
            self.failure_count += 1


_active_publisher: ContextVar[EventPublisher | None] = ContextVar("career_events", default=None)


@contextmanager
def execution_run(bus: EventBus, session_id: str) -> Iterator[EventPublisher]:
    publisher = EventPublisher(bus, session_id)
    token = _active_publisher.set(publisher)
    try:
        yield publisher
    finally:
        _active_publisher.reset(token)


def emit_event(event_type: str, *, duration_ms: int | None = None, **payload) -> None:
    publisher = _active_publisher.get()
    if publisher is not None:
        publisher.emit(event_type, duration_ms=duration_ms, **payload)
