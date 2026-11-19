from threading import Lock
import json

from openai import APIConnectionError, APIStatusError, APITimeoutError

from backend.app.career_agent import CareerSession, build_graph, tool_outcome
from backend.app.execution_events import EventBus
from backend.app.integrations.gms import build_gms_model
from backend.app.logging_agent import LoggingAgent
from backend.app.workspace_service import AppProblem, WorkspaceService


class ChatService:
    def __init__(self, *, model_factory=build_gms_model, workspace=None, bus=None):
        self.model_factory = model_factory
        self.workspace = workspace or WorkspaceService()
        self.bus = bus if bus is not None else EventBus()
        if bus is None:
            self.bus.subscribe(LoggingAgent())
        self._contexts = {}
        self._locks = {}
        self._guard = Lock()

    def invoke(self, session_id, message, profile_id=None, assess_fit=True):
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
            try:
                model = self.model_factory()
            except RuntimeError:
                raise AppProblem(503, "MISSING_API_KEY", ".env에 GMS_API_KEY를 설정한 뒤 서버를 다시 시작해 주세요.") from None
            session = CareerSession(build_graph(model, profile_id=profile_id if assess_fit else None), event_bus=self.bus)
            previous = self._contexts.get(session_id)
            if previous and previous[0] == profile_id:
                session.contexts[session_id] = previous[1]
            profile_context = None if profile is None else {
                field: profile[field] for field in (
                    "desired_role", "preferred_locations", "preferred_employment_types",
                )
            }
            state = session.invoke(session_id, message, profile_context=profile_context)
            if session_id in session.contexts:
                self._contexts[session_id] = (profile_id, session.contexts[session_id])
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
            return {
                "session_id": session_id, "run_id": state["run_id"], "answer": state["answer"],
                "analysis": state["analysis"], "used_previous_context": state["used_previous_context"],
                "jobs": list(jobs.values()), "matches": state.get("match_results", []),
                "excluded_jobs": excluded, "tool_calls": calls,
                "search_summary": state.get("search_summary"),
                "replan_count": state.get("replan_count", 0), "events": received,
            }
        except AppProblem:
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
