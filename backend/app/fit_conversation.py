"""Hold low reference scores until the user has considered missing experiences."""

from contextlib import closing
from copy import deepcopy
from datetime import datetime
from time import monotonic, perf_counter_ns

from backend.app.db.job_search import KST
from backend.app.execution_events import execution_run, emit_event
from backend.app.fit_policy import needs_experience
from backend.app.job_fit import evaluate_match
from backend.app.match_service import PostingCriteria, evaluate_and_save
from backend.app.workspace_service import AppProblem


def public_match(match):
    return {key: value for key, value in match.items() if not key.startswith("_")}


def prepare_review(service, result, profile_id, *, disclose=False):
    matches = result.get("matches", [])
    waiting = [m for m in matches if m.get("current") and needs_experience(m) and not disclose]
    if waiting:
        service._pending_fits[result["session_id"]] = {
            "profile_id": profile_id, "response": deepcopy(result), "expires": monotonic() + 1800,
        }
        gaps = list(dict.fromkeys(gap for m in waiting for gap in m["current"].get("gaps", [])))[:5]
        questions = [f"{gap}와 연결되는 프로젝트·업무·교육 경험이 있나요?" for gap in gaps]
        if not questions:
            questions = ["아직 프로필에 정리하지 않은 관련 프로젝트·업무·교육 경험이 있나요?"]
        result["fit_follow_up"] = {"job_ids": [m["job_id"] for m in waiting], "questions": questions,
                                   "reason": "missing_experience_check", "scores_withheld": True}
        result["matches"] = [
            {"job_id": m["job_id"], "title": m["title"], "requires_experience_follow_up": True,
             "evidence_source": m.get("evidence_source", "posting")}
            if m in waiting else public_match(m) for m in matches
        ]
        result["answer"] = ("점수를 안내하기 전에 관련 경험을 더 확인하고 싶어요.\n" + "\n".join(questions)
                            + "\n경험 추가·정리에서 실제로 한 일과 사용 기술을 정리해 주세요. 확인한 내용을 저장하면 다시 살펴봅니다. "
                            "추가 경험이 없다면 '추가 경험 없음'을 선택해 주세요. 그때 현재 준비 상황과 참고 적합도를 함께 안내하겠습니다.")
    else:
        result["matches"] = [public_match(m) for m in matches]
        result["fit_follow_up"] = None
        if matches or any(name in {"search_jobs", "get_job_detail"} for name in result.get("analysis", {}).get("intents", [])):
            service._pending_fits.pop(result["session_id"], None)
        if disclose:
            lines = ["추가 경험이 없다는 답변을 반영했습니다. 지금까지 정리한 경험을 기준으로 다음 준비를 안내합니다."]
            for match in matches:
                if not match.get("current"):
                    continue
                current, future = match["current"], match["future"]
                lines.append(f"{match['title']}: 현재 참고 적합도 {current['score']}/{current['assessed_weight']}점, "
                             f"미래 예상 {future['score']}/{future['assessed_weight']}점.")
                gaps = current.get("gaps", [])
                if gaps:
                    lines.append("우선 준비: " + ", ".join(gaps) + ".")
                    if any(gap.startswith("기술:") for gap in gaps):
                        lines.append("기술 요건은 작은 실습이나 프로젝트에서 적용하고 실제 행동과 결과를 경험으로 기록해 보세요.")
                    if any(gap.startswith("자격:") for gap in gaps):
                        lines.append("자격·면허 요건은 공식 취득 요건과 시험 일정을 먼저 확인해 보세요.")
                if current.get("unknown") or current.get("blockers"):
                    lines.append("확인할 사항: " + ", ".join(current.get("unknown", []) + current.get("blockers", [])))
            lines.append("이 값은 확인된 배점의 참고 정보이며 합격 가능성이나 지원 포기 여부를 판정하지 않습니다. 준비 계획으로 다음 행동을 정해 보세요.")
            result["answer"] = "\n".join(lines)
    if waiting and any(row.get("status") == "error" for row in result.get("source_results", [])):
        result["answer"] += "\n일부 출처를 조회하지 못했으므로 확보한 공고를 기준으로 대화하고 있습니다."
    return result


def resume_review(service, session_id, profile_id, action):
    pending = service._pending_fits.get(session_id)
    if pending is None or pending["profile_id"] != profile_id or pending["expires"] < monotonic():
        raise AppProblem(409, "NO_PENDING_FIT_REVIEW", "이 프로필에서 이어갈 경험 확인 요청이 없습니다. 공고 상담을 다시 시작해 주세요.")
    result = deepcopy(pending["response"])
    start = perf_counter_ns()
    with execution_run(service.bus, session_id) as publisher:
        emit_event("run_started")
        emit_event("stage_started", stage="assess_fit")
        try:
            matches = []
            for match in result["matches"]:
                if not match.get("_criteria"):
                    matches.append(match); continue
                criteria = PostingCriteria(**{key: tuple(value) if isinstance(value, list) else value
                                             for key, value in match["_criteria"].items()})
                with closing(service.workspace.connection_factory()) as db:
                    fit = evaluate_match(db, profile_id, match["job_id"], criteria, datetime.now(KST).date(),
                                         job_overrides=match.get("_job_overrides"))
                    job_source = db.execute("SELECT source FROM job_posting WHERE id=?", (match["job_id"],)).fetchone()[0]
                    if job_source == "alio" and (action == "none" or not needs_experience(fit)):
                        evaluate_and_save(db, profile_id, match["job_id"], criteria, datetime.now(KST).date())
                        db.commit()
                match.update(fit)
                matches.append(match)
                if action == "none" or not needs_experience(fit):
                    emit_event("fit_decided", job_id=match["job_id"], current_score=fit["current"]["score"],
                               future_score=fit["future"]["score"], reason_code="experience_review_completed")
                else:
                    emit_event("fit_decided", job_id=match["job_id"], reason_code="experience_followup_needed")
            result.update(matches=matches, run_id=publisher.run_id, used_previous_context=True)
            result = prepare_review(service, result, profile_id, disclose=action == "none")
            if action == "recheck" and result["fit_follow_up"] is None:
                result["answer"] = "저장한 경험을 반영해 준비 상황을 다시 정리했습니다. 경험에서 확인되는 요건이 늘었습니다. 공고 카드의 확인 사항을 살펴보고 다음 준비 계획을 정해 보세요."
            emit_event("stage_finished", stage="assess_fit", status="completed", duration_ms=(perf_counter_ns()-start)//1_000_000)
            emit_event("run_finished", status="completed", duration_ms=(perf_counter_ns()-start)//1_000_000)
            return result
        except Exception:
            emit_event("error", stage="assess_fit", error_code="FIT_ERROR", reason_code="stage_error")
            emit_event("stage_finished", stage="assess_fit", status="failed", duration_ms=(perf_counter_ns()-start)//1_000_000)
            emit_event("run_finished", status="failed", duration_ms=(perf_counter_ns()-start)//1_000_000)
            raise
