from threading import Lock
import json
import re
from copy import deepcopy
from time import monotonic

from openai import APIConnectionError, APIStatusError, APITimeoutError

from backend.app.career_agent import CareerSession, build_graph, tool_outcome
from backend.app.execution_events import EventBus
from backend.app.integrations.gms import build_gms_model
from backend.app.logging_agent import LoggingAgent
from backend.app.workspace_service import AppProblem, WorkspaceService
from backend.app.rss_fit import selected_rss_job, evaluate_submission
from backend.app.fit_conversation import prepare_review, resume_review
from backend.app.search_support import suggested_terms


class ChatService:
    def __init__(self, *, model_factory=build_gms_model, workspace=None, bus=None):
        self.model_factory = model_factory
        self.workspace = workspace or WorkspaceService()
        self.bus = bus if bus is not None else EventBus()
        if bus is None:
            self.bus.subscribe(LoggingAgent())
        self._contexts = {}
        self._pending_fits = {}
        self._locks = {}
        self._guard = Lock()

    def invoke(self, session_id, message, profile_id=None, assess_fit=True, *, job_source="all",
               requirements_job_id=None, original_confirmed=False, requirements_complete=False,
               recruitment_period_status=None, recruitment_start=None, recruitment_end=None, fit_follow_up_action=None):
        profile = self.workspace.profile(profile_id) if profile_id is not None else None
        with self._guard:
            lock = self._locks.setdefault(session_id, Lock())
        if not lock.acquire(blocking=False):
            raise AppProblem(409, "SESSION_BUSY", "이 대화의 이전 요청을 처리 중입니다. 완료 후 다시 보내 주세요.")
        received = []

        def collect(event):
            if event.session_id == session_id:
                received.append({
                    "session_id": event.session_id, "run_id": event.run_id,
                    "event_type": event.event_type, "created_at": event.created_at,
                    "duration_ms": event.duration_ms, "payload": json.loads(event.payload),
                })

        unsubscribe = self.bus.subscribe(collect)
        try:
            pending = self._pending_fits.get(session_id)
            natural_none = bool(re.fullmatch(r"(?:(?:네|아니요|아뇨|음)[,\s]*)?(?:(?:더(?:\s*이상)?|추가(?:로)?|관련|그런|다른)\s*)*(?:경험(?:은|이|도)?\s*)?(?:(?:더|추가로)\s*)?(?:없어요|없어|없습니다|없음|없다|없네요)[.! ]*", message.strip()))
            action = fit_follow_up_action or ("none" if pending and natural_none else None)
            if action:
                result = resume_review(self, session_id, profile_id, action)
                return {**result, "events": received}
            selected = (selected_rss_job(self.workspace, requirements_job_id, profile_id, original_confirmed)
                        if requirements_job_id is not None else None)
            try:
                model = self.model_factory()
            except RuntimeError:
                raise AppProblem(503, "MISSING_API_KEY", ".env에 GMS_API_KEY를 설정한 뒤 서버를 다시 시작해 주세요.") from None
            if (pending and pending["profile_id"] == profile_id and pending["expires"] >= monotonic() and selected is None
                    and re.search(r"경험|프로젝트|교육|업무|담당|구현|사용|배웠|있어요|있어", message)
                    and not re.search(r"공고|찾아|추천|검색해|검색하|검색어", message)):
                from backend.app.coaching_api import CoachingService, CoachInput
                history = pending.get("experience_chat", [])[-18:] + [{"role": "user", "content": message}]
                coaching = CoachingService(self.model_factory, bus=self.bus).coach(CoachInput(session_id=session_id, messages=history))
                result = prepare_review(self, deepcopy(pending["response"]), profile_id)
                self._pending_fits[session_id]["experience_chat"] = history + [{"role": "assistant", "content": coaching.reply}]
                result.update(answer=coaching.reply + "\n초안을 검토하고 경험으로 저장한 뒤 상담을 이어갈 수 있어요.",
                              experience_draft=coaching.draft.model_dump(mode="json") if coaching.draft else None,
                              run_id=received[0]["run_id"] if received else result["run_id"])
                return {**result, "events": received}
            if selected is not None:
                result = evaluate_submission(model, self.workspace, self.bus, session_id, selected,
                                             profile_id, message, requirements_complete,
                                             recruitment_period_status or "unknown", recruitment_start, recruitment_end)
                return {**prepare_review(self, result, profile_id), "events": received}
            session = CareerSession(build_graph(model, profile_id=profile_id if assess_fit else None), event_bus=self.bus)
            previous = self._contexts.get(session_id)
            if previous and previous[0] == profile_id and previous[2] == job_source:
                session.contexts[session_id] = previous[1]
            profile_context = None if profile is None else {
                field: profile[field] for field in (
                    "desired_role", "preferred_locations", "preferred_employment_types",
                )
            }
            if profile_context is not None:
                profile_context["experiences"] = [
                    {key: e.get(key) for key in ("title", "role", "skills", "action", "result")}
                    for e in self.workspace.experiences(profile_id)["experiences"][-10:]
                ]
            state = session.invoke(session_id, message, profile_context=profile_context, job_source=job_source)
            if session_id in session.contexts:
                self._contexts[session_id] = (profile_id, session.contexts[session_id], job_source)
            jobs = {job["id"]: dict(job) for job in state.get("eligible_jobs", [])}
            all_jobs = {}
            for record in state.get("tool_results", []):
                if record["result"]["ok"]:
                    data = record["result"]["data"]
                    if record["tool"] == "search_jobs":
                        all_jobs.update({job["id"]: job for job in data["jobs"]})
                    elif record["tool"] == "get_job_detail":
                        if data["id"] in jobs or "search_jobs" not in state["analysis"]["intents"]:
                            jobs.setdefault(data["id"], {}).update(data)
            excluded = [
                {**item, "title": all_jobs.get(item["job_id"], {}).get("title", "공고")}
                for item in state.get("evaluation", []) if item["deadline_status"] in {"unknown", "unmet"}
            ]
            calls = [
                {"tool": record["tool"], "status": tool_outcome(record["result"]),
                 "error_code": record["result"].get("error", {}).get("code")}
                for record in state.get("tool_results", [])
            ]
            result = {
                "session_id": session_id, "run_id": state["run_id"], "answer": state["answer"],
                "analysis": state["analysis"], "used_previous_context": state["used_previous_context"],
                "jobs": list(jobs.values()), "matches": state.get("match_results", []),
                "excluded_jobs": excluded, "tool_calls": calls,
                "search_summary": state.get("search_summary"),
                "replan_count": state.get("replan_count", 0), "events": received,
                "reference_status": state.get("reference_status", "current"),
                "plan_draft": state.get("plan_draft"),
            }
            source_results = []
            for record in state.get("tool_results", []):
                if record["tool"] == "search_jobs":
                    source_results = record["result"].get("data", {}).get("source_results", record["result"].get("source_results", [])) or source_results
            result["source_results"] = source_results
            if any(job.get('unverified_filters') for job in result['jobs']):
                result['answer']+='\n추가 출처의 일부 공고는 요청한 고용형태·경력·NCS 조건을 확인하지 못한 참고 후보입니다. 원문에서 확인해 주세요.'
            duplicate_count=max((record['result'].get('data',{}).get('duplicate_count',record['result'].get('duplicate_count',0)) for record in state.get('tool_results',[]) if record['tool']=='search_jobs'),default=0)
            if duplicate_count:result['answer']+=f'\n수집한 인크루트 RSS와 중복된 추가 출처 공고 {duplicate_count}건은 제외했습니다.'
            related_count = sum(job.get("search_match", {}).get("level") == "related" for job in result["jobs"])
            if related_count:
                result["answer"] += f"\n검색 결과 중 {related_count}건은 출처의 분야 분류만 일치하는 관련 후보입니다. 구체적인 모집 직무는 원문에서 확인해 주세요."
            summary = result.get("search_summary") or {}
            if summary.get("search_status") == "no_result":
                suggestions = suggested_terms(state["analysis"].get("keyword"))
                result["search_suggestions"] = suggestions
                result["answer"] += "\n검색어를 넓혀 보세요: " + ", ".join(suggestions) + ". 아래 검색어를 선택하면 다시 검색할 수 있습니다."
            if any(row["status"] == "error" for row in source_results):
                result["answer"] += "\n일부 출처를 조회하지 못했습니다. 확보한 결과만 표시하며, 전체 검색 결과가 0건이라고 확정하지 않습니다."
            return prepare_review(self, result, profile_id)
        except AppProblem as error:
            if received:
                error.details.update(session_id=session_id, run_id=received[0]["run_id"], events=received)
            raise
        except APITimeoutError:
            raise self._failure(504, "GMS_TIMEOUT", "AI 응답 시간이 초과됐습니다. 잠시 후 다시 시도해 주세요.", session_id, received) from None
        except APIConnectionError:
            raise self._failure(502, "GMS_CONNECTION_ERROR", "AI 서버에 연결하지 못했습니다. 네트워크 상태를 확인해 주세요.", session_id, received) from None
        except APIStatusError as error:
            code = "GMS_AUTH_ERROR" if error.status_code in {401, 403} else "GMS_UPSTREAM_ERROR"
            raise self._failure(502, code, "AI 서버가 요청을 처리하지 못했습니다. GMS 설정과 이용 상태를 확인해 주세요.", session_id, received) from None
        except Exception:
            raise self._failure(502, "AGENT_ERROR", "대화를 완료하지 못했습니다. 실행 기록을 확인하고 다시 시도해 주세요.", session_id, received) from None
        finally:
            unsubscribe()
            lock.release()

    @staticmethod
    def _failure(status, code, message, session_id, events):
        return AppProblem(status, code, message, session_id=session_id,
                          run_id=events[0]["run_id"] if events else None, events=events)
