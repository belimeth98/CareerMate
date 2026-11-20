"""Evaluate user-supplied evidence in memory, without changing shared jobs."""

import json
from contextlib import closing
from dataclasses import replace, asdict
from datetime import datetime
from time import perf_counter_ns

from backend.app.db.job_search import KST
from backend.app.execution_events import emit_event, execution_run
from backend.app.job_fit import evaluate_match
from backend.app.match_service import extract_job_criteria
from backend.app.workspace_service import AppProblem
from backend.app.fit_policy import needs_experience


def selected_rss_job(workspace, job_id, profile_id, confirmed):
    if profile_id is None:
        raise AppProblem(422, "PROFILE_REQUIRED", "적합도를 판단하려면 프로필을 먼저 저장하거나 선택해 주세요.")
    if not confirmed:
        raise AppProblem(422, "ORIGINAL_CONFIRMATION_REQUIRED", "원문 공고를 확인한 뒤 원문 확인 항목에 체크해 주세요.")
    with closing(workspace.connection_factory()) as db:
        row = db.execute("SELECT * FROM job_posting WHERE id=?", (job_id,)).fetchone()
    if row is None:
        raise AppProblem(404, "JOB_NOT_FOUND", "선택한 공고를 찾을 수 없습니다. 다시 검색해 주세요.")
    job = dict(row)
    if job["source"] != "incruit" or job["board_id"] != "rss":
        raise AppProblem(422, "INVALID_JOB_SOURCE", "이 입력 기능은 인크루트 RSS 공고에 사용합니다.")
    for name in ("location", "employment_type", "career_type", "ncs_categories"):
        job[name] = json.loads(job[name]) if job[name] else None
    # Do not return cached arbitrary description HTML to the client here.
    return {key: job[key] for key in (
        "id", "source", "board_id", "source_job_id", "company", "title", "location",
        "employment_type", "career_type", "ncs_categories", "deadline", "is_open", "job_url",
    )}


def evaluate_submission(model, workspace, bus, session_id, job, profile_id, text, complete,
                        period_status="unknown", recruitment_start=None, recruitment_end=None):
    overrides = {}
    if period_status == "provided":
        if not recruitment_start or not recruitment_end or recruitment_end < recruitment_start:
            raise AppProblem(422, "INVALID_RECRUITMENT_PERIOD", "원문 모집 시작일과 종료일을 확인해 주세요.")
        overrides = {"deadline": recruitment_end.isoformat(), "recruitment_start": recruitment_start.isoformat()}
    elif period_status == "until_filled":
        overrides = {"deadline": None}
    job = {**job, **overrides, "recruitment_period_status": period_status, "period_source": "user_submitted"}
    start = perf_counter_ns()
    with execution_run(bus, session_id) as publisher:
        emit_event("run_started", source="incruit", evidence_source="user_submitted")
        stage = "analyze"
        stage_start = perf_counter_ns()
        try:
            emit_event("stage_started", stage=stage)
            emit_event("intent_decided", stage=stage, job_id=job["id"],
                       evidence_source="user_submitted", reason_code="user_requirements_received")
            emit_event("stage_finished", stage=stage, status="completed", duration_ms=0)
            stage = "assess_fit"
            stage_start = perf_counter_ns()
            emit_event("stage_started", stage=stage)
            criteria = extract_job_criteria(model, {
                "title": job["title"], "required_qualifications": [text],
                "preferred_qualifications": None, "description_html": None,
            })
            if not any((criteria.required_skills, criteria.required_certifications,
                        criteria.preferred_skills, criteria.preferred_certifications,
                        criteria.min_career_months is not None, criteria.other_required,
                        criteria.other_preferred, criteria.project_keywords)):
                raise AppProblem(422, "NO_EVALUABLE_REQUIREMENTS", "필수·우대 자격요건을 구체적으로 입력해 주세요. 입력만으로 평가할 조건을 확인하지 못했습니다.")
            if not complete:
                criteria = replace(criteria, requirements_checked=False)
            with closing(workspace.connection_factory()) as db:
                fit = evaluate_match(db, profile_id, job["id"], criteria, datetime.now(KST).date(), job_overrides=overrides)
            match = {"job_id": job["id"], "title": job["title"], **fit,
                     "other_preferred": list(criteria.other_preferred),
                     "evidence_source": "user_submitted", "requirements_complete": complete,
                     "saved": False, "_criteria": asdict(criteria), "_job_overrides": overrides,
                     "requires_experience_follow_up": needs_experience(fit)}
            if needs_experience(fit):
                emit_event("fit_decided", job_id=job["id"], source="incruit", reason_code="experience_followup_needed")
            else:
                emit_event("fit_decided", job_id=job["id"], source="incruit", evidence_source="user_submitted",
                       current_score=fit["current"]["score"], future_score=fit["future"]["score"],
                       current_eligibility=fit["current"]["eligibility"],
                       future_eligibility=fit["future"]["eligibility"], reason_code="user_requirements_checked")
            emit_event("stage_finished", stage=stage, status="completed",
                       duration_ms=(perf_counter_ns() - stage_start) // 1_000_000)
            stage = "respond"
            stage_start = perf_counter_ns()
            emit_event("stage_started", stage=stage)
            current, future = fit["current"], fit["future"]
            answer = (f"{job['title']}\n사용자가 입력한 자격요건 기준의 참고 평가입니다. "
                      "입력 내용과 원문의 일치 여부는 자동 검증하지 않았습니다.\n"
                      "경험과 연결되는 요건, 추가로 확인할 조건을 정리했습니다. 참고 점수는 공고 카드에서 펼쳐 볼 수 있습니다.\n"
                      "진행 상태·고용형태·마감일 등 미확인 정보는 충족으로 간주하지 않습니다. "
                      "마감일을 확인할 수 없으면 마감 전 성장 계획도 반영하지 않습니다.\n"
                      + ("필수요건 일부만 입력해 전체 지원 자격은 확정하지 않았습니다.\n" if not complete else "")
                      + "누락 역량과 추가 확인 사항은 공고 카드에서 확인해 주세요. 입력 원문과 평가 결과는 공용 공고·평가 DB에 저장하지 않았습니다.")
            emit_event("stage_finished", stage=stage, status="completed",
                       duration_ms=(perf_counter_ns() - stage_start) // 1_000_000)
            emit_event("run_finished", status="completed", duration_ms=(perf_counter_ns() - start) // 1_000_000)
            return {"session_id": session_id, "run_id": publisher.run_id, "answer": answer,
                    "analysis": {"intents": [], "job_id": job["id"], "evidence_source": "user_submitted"},
                    "used_previous_context": False, "jobs": [job], "matches": [match],
                    "excluded_jobs": [], "tool_calls": [], "search_summary": None, "replan_count": 0,
                    "reference_status": "current", "plan_draft": None}
        except Exception:
            emit_event("error", stage=stage, error_code="FIT_ERROR", reason_code="stage_error")
            emit_event("stage_finished", stage=stage, status="failed", duration_ms=(perf_counter_ns()-stage_start)//1_000_000)
            emit_event("run_finished", status="failed", duration_ms=(perf_counter_ns()-start)//1_000_000)
            raise
