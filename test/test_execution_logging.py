"""F3012/F3013 regression checks. No network, API keys, or production DB writes.

Run: python -m unittest discover -s test -p test_execution_logging.py -v
"""

import json
import sqlite3
import tempfile
import unittest
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.app import career_agent as agent
from backend.app.execution_events import EventBus, EventPublisher
from backend.app.logging_agent import LoggingAgent


class FakeModel:
    def __init__(self, analyses=None):
        self.analyses = list(analyses or [{"intents": ["search_jobs"], "keyword": "약사", "limit": 1}])
        self.responses = 0

    def with_structured_output(self, schema, **kwargs):
        return SimpleNamespace(invoke=self.analyze)

    def analyze(self, messages):
        return agent.RequestAnalysis.model_validate(self.analyses.pop(0))

    def invoke(self, messages):
        self.responses += 1
        return SimpleNamespace(text="조회한 공고를 안내합니다.")


class FakeTool:
    def __init__(self, fn):
        self.fn = fn
        self.calls = []

    def invoke(self, args):
        self.calls.append(dict(args))
        return self.fn(args)


def job(job_id=1, deadline="2098-01-01"):
    return {"id": job_id, "title": "약사 채용", "deadline": deadline, "company": "예시기관"}


def found(jobs):
    return {"ok": True, "data": {"jobs": jobs, "count": len(jobs)}}


def failure(code="NO_RESULT"):
    return {"ok": False, "error": {"code": code, "message": "secret-error-message"}}


class ExecutionLoggingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "logs.sqlite3"
        schema = Path(agent.__file__).parent / "db" / "schema.sql"
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript(schema.read_text(encoding="utf-8"))
        self.bus = EventBus()
        self.events = []
        self.bus.subscribe(self.events.append)
        self.logger = LoggingAgent(lambda: sqlite3.connect(self.path))
        self.bus.subscribe(self.logger)

    def session(self, model=None, **kwargs):
        return agent.CareerSession(agent.build_graph(model or FakeModel(), **kwargs), event_bus=self.bus)

    def payloads(self, event_type):
        return [json.loads(event.payload) for event in self.events if event.event_type == event_type]

    def assert_complete(self, result):
        self.assertTrue(result["answer"])
        self.assertEqual(self.events[-1].event_type, "run_finished")
        self.assertEqual(json.loads(self.events[-1].payload)["status"], "completed")

    def test_search_event_order_timing_and_exact_sqlite_storage(self):
        tool = FakeTool(lambda args: found([job()]))
        with patch.dict(agent.TOOLS, search_jobs=tool):
            result = self.session().invoke("normal", "약사 공고 1건")
        self.assert_complete(result)
        self.assertEqual(self.events[0].event_type, "run_started")
        self.assertEqual(
            [item["stage"] for item in self.payloads("stage_started")],
            ["analyze", "plan", "execute", "evaluate", "respond"],
        )
        requested, = self.payloads("tool_requested")
        finished, = self.payloads("tool_finished")
        self.assertEqual(requested["call_id"], finished["call_id"])
        self.assertEqual(finished["count"], 1)
        self.assertEqual(finished["status"], "ok")
        analyze = self.payloads("stage_finished")[0]
        self.assertEqual(analyze["intents"], ["search_jobs"])
        self.assertTrue(analyze["reason"])
        for event in self.events:
            if event.event_type in {"stage_finished", "tool_finished", "run_finished"}:
                self.assertIsInstance(event.duration_ms, int)
                self.assertGreaterEqual(event.duration_ms, 0)
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM execution_event ORDER BY id").fetchall()
        self.assertEqual(len(rows), len(self.events))
        for seq, (event, row) in enumerate(zip(self.events, rows), start=1):
            stored = dict(row)
            stored.pop("id")
            self.assertEqual(stored, asdict(event))
            self.assertEqual(json.loads(row["payload"])["seq"], seq)

    def test_follow_up_same_session_different_runs_and_other_session(self):
        model = FakeModel([
            {"intents": ["search_jobs"], "keyword": "약사", "region": "강원", "employment_type": "정규직", "limit": 1},
            {"region": "서울", "is_follow_up": True, "decision_reason": "follow_up"},
            {"intents": ["search_jobs"], "keyword": "개발자"},
        ])
        session = self.session(model)
        with patch.dict(agent.TOOLS, search_jobs=FakeTool(lambda args: found([job()]))):
            first = session.invoke("same-session", "첫 요청")
            second = session.invoke("same-session", "서울로 바꿔줘")
            other = session.invoke("other-session", "다른 대화")
        self.assertEqual(first["session_id"], second["session_id"])
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertNotEqual(first["run_id"], other["run_id"])
        self.assertTrue(second["used_previous_context"])
        for key, value in {"keyword": "약사", "region": "서울", "employment_type": "정규직", "limit": 1}.items():
            self.assertEqual(second["analysis"][key], value)
        self.assertEqual({e.session_id for e in self.events}, {"same-session", "other-session"})

    def test_replan_expands_same_conditions_at_most_twice(self):
        model = FakeModel([{
            "intents": ["search_jobs"], "keyword": "약사", "region": "강원",
            "employment_type": "정규직", "limit": 1, "deadline_on_or_after": "2099-01-01",
        }])
        tool = FakeTool(lambda args: found([job(i) for i in range(1, args["limit"] + 1)]))
        with patch.dict(agent.TOOLS, search_jobs=tool):
            result = self.session(model).invoke("replan", "마감 조건")
        self.assert_complete(result)
        self.assertEqual([args["limit"] for args in tool.calls], [1, 11, 21])
        self.assertEqual([args["refresh"] for args in tool.calls], [True, False, False])
        self.assertEqual(result["replan_count"], 2)
        self.assertEqual(len(self.payloads("replan_requested")), 2)
        self.assertEqual(result["search_summary"]["deadline_status"], "unmet")
        self.assertIn("마감 조건 미충족", result["answer"])
        for args in tool.calls:
            self.assertEqual(args["keyword"], "약사")
            self.assertEqual(args["region"], "강원")
            self.assertEqual(args["employment_type"], "정규직")

    def test_replan_stops_when_eligible_candidate_is_found(self):
        model = FakeModel([{"intents": ["search_jobs"], "limit": 1, "deadline_on_or_after": "2099-01-01"}])
        def search(args):
            jobs = [job(i) for i in range(1, args["limit"] + 1)]
            if args["limit"] > 1:
                jobs[-1]["deadline"] = "2100-01-01"
            return found(jobs)
        with patch.dict(agent.TOOLS, search_jobs=FakeTool(search)):
            result = self.session(model).invoke("replan-success", "마감 조건")
        self.assertEqual(result["replan_count"], 1)
        self.assertEqual(result["eligible_jobs"][0]["id"], 11)
        self.assertEqual(self.payloads("constraint_decided")[-1]["deadline_status"], "met")

    def test_no_result_is_zero_and_never_deadline_failure(self):
        model = FakeModel()
        with patch.dict(agent.TOOLS, search_jobs=FakeTool(lambda args: failure())):
            result = self.session(model).invoke("zero", "서울 정규직 약사")
        self.assert_complete(result)
        self.assertIn("검색 결과 0건", result["answer"])
        self.assertNotIn("마감 조건 미충족", result["answer"])
        self.assertEqual(model.responses, 0)
        self.assertEqual(self.payloads("tool_finished")[0]["status"], "no_result")
        self.assertEqual(self.payloads("constraint_decided")[0]["deadline_status"], "not_requested")
        self.assertEqual(self.payloads("error"), [])

    def test_empty_success_response_also_means_zero(self):
        with patch.dict(agent.TOOLS, search_jobs=FakeTool(lambda args: found([]))):
            result = self.session().invoke("empty", "검색")
        self.assertIn("검색 결과 0건", result["answer"])

    def test_no_candidates_with_deadline_is_not_a_failed_deadline_check(self):
        model = FakeModel([{"intents": ["search_jobs"], "deadline_on_or_after": "2099-01-01"}])
        with patch.dict(agent.TOOLS, search_jobs=FakeTool(lambda args: failure())):
            result = self.session(model).invoke("zero-with-deadline", "마감 조건")
        self.assertEqual(result["search_summary"]["deadline_status"], "not_evaluated")
        self.assertNotIn("마감 조건 미충족", result["answer"])

    def test_unknown_deadline_is_distinct_from_unmet(self):
        model = FakeModel([{"intents": ["search_jobs"], "deadline_on_or_after": "2099-01-01"}])
        with patch.dict(agent.TOOLS, search_jobs=FakeTool(lambda args: found([job(deadline=None)]))):
            result = self.session(model).invoke("unknown", "마감 조건")
        self.assertEqual(result["search_summary"]["deadline_status"], "unknown")
        self.assertEqual(result["search_summary"]["unmet_count"], 0)
        self.assertEqual(result["search_summary"]["unknown_count"], 1)

    def test_tool_failures_are_errors_not_zero_results(self):
        def throws(args):
            raise RuntimeError("secret-in-exception")
        for fn in (lambda args: failure("DATABASE_ERROR"), throws):
            with self.subTest(fn=fn), patch.dict(agent.TOOLS, search_jobs=FakeTool(fn)):
                result = self.session().invoke("tool-failure", "검색")
            self.assert_complete(result)
            self.assertEqual(result["search_summary"]["search_status"], "error")
            self.assertNotIn("검색 결과 0건", result["answer"])
            self.assertEqual(self.payloads("tool_finished")[-1]["status"], "error")
        self.assertNotIn("secret-in-exception", "".join(e.payload for e in self.events))

    def test_logging_storage_failure_does_not_interrupt_conversation(self):
        def broken_connection():
            raise sqlite3.OperationalError("secret-db-path")
        self.bus.subscribe(LoggingAgent(broken_connection))
        later = []
        self.bus.subscribe(later.append)
        with patch.dict(agent.TOOLS, search_jobs=FakeTool(lambda args: found([job()]))):
            result = self.session().invoke("storage-failure", "검색")
        self.assert_complete(result)
        self.assertEqual(self.bus.failure_count, len(self.events))
        self.assertEqual(later, self.events)
        self.assertEqual(self.logger.stored_count, len(self.events))

    def test_malformed_tool_response_still_has_a_finished_event(self):
        for response in (None, {"ok": True, "data": {}}, found([{"id": 1}])):
            with self.subTest(response=response):
                with patch.dict(agent.TOOLS, search_jobs=FakeTool(lambda args: response)):
                    result = self.session().invoke("malformed", "검색")
                self.assert_complete(result)
                self.assertEqual(self.payloads("tool_finished")[-1]["error_code"], "INVALID_TOOL_RESPONSE")

    def test_real_sqlite_missing_table_failure_also_continues(self):
        self.bus.subscribe(LoggingAgent(lambda: sqlite3.connect(":memory:")))
        with patch.dict(agent.TOOLS, search_jobs=FakeTool(lambda args: found([job()]))):
            result = self.session().invoke("missing-table", "검색")
        self.assert_complete(result)
        self.assertEqual(self.bus.failure_count, len(self.events))

    def test_fit_detail_tool_failure_is_recorded_and_response_completes(self):
        with patch.dict(agent.TOOLS, search_jobs=FakeTool(lambda args: found([job()])), get_job_detail=FakeTool(lambda args: failure("UPSTREAM_TIMEOUT"))):
            result = self.session(demo_fit=True).invoke("fit-tool-error", "검색 후 적합도")
        self.assert_complete(result)
        self.assertEqual(result["match_results"][0]["error"]["code"], "UPSTREAM_TIMEOUT")
        event = self.payloads("tool_finished")[-1]
        self.assertEqual(event["stage"], "assess_fit")
        self.assertEqual(event["status"], "error")

    def test_unsubscribe_removes_logging_without_affecting_execution(self):
        received = []
        unsubscribe = self.bus.subscribe(received.append)
        unsubscribe()
        unsubscribe()
        with patch.dict(agent.TOOLS, search_jobs=FakeTool(lambda args: found([job()]))):
            self.assert_complete(self.session().invoke("unsubscribed", "검색"))
        self.assertEqual(received, [])

    def test_missing_subscribers_do_not_interrupt_conversation(self):
        session = agent.CareerSession(agent.build_graph(FakeModel()))
        with patch.dict(agent.TOOLS, search_jobs=FakeTool(lambda args: found([job()]))):
            self.assertTrue(session.invoke("no-subscriber", "검색")["answer"])
        self.assertEqual(session.event_bus.failure_count, 0)

    def test_fit_detail_call_is_logged_and_retained_in_tool_history(self):
        detail = FakeTool(lambda args: {"ok": True, "data": {**job(), "required_qualifications": []}})
        fit = {
            "current": {"score": 65, "assessed_weight": 65, "eligibility": "unknown"},
            "future": {"score": 65, "eligibility": "unknown"},
            "applied_plans": [], "criteria": {"other_preferred": []},
        }
        with patch.dict(agent.TOOLS, search_jobs=FakeTool(lambda args: found([job()])), get_job_detail=detail):
            with patch.object(agent, "score_posting", return_value=fit):
                result = self.session(demo_fit=True).invoke("fit", "검색 후 적합도")
        self.assert_complete(result)
        calls = self.payloads("tool_requested")
        self.assertEqual(calls[-1]["stage"], "assess_fit")
        self.assertEqual(calls[-1]["tool"], "get_job_detail")
        self.assertEqual(result["tool_results"][-1]["tool"], "get_job_detail")
        self.assertEqual(self.payloads("fit_decided")[0]["current_score"], 65)

    def test_cached_detail_is_not_called_again_during_fit(self):
        model = FakeModel([{"intents": ["search_jobs", "get_job_detail"], "limit": 1}])
        detail = FakeTool(lambda args: {"ok": True, "data": job()})
        with patch.dict(agent.TOOLS, search_jobs=FakeTool(lambda args: found([job()])), get_job_detail=detail):
            with patch.object(agent, "score_posting", side_effect=RuntimeError("secret-fit")):
                result = self.session(model, demo_fit=True).invoke("cached", "검색 후 상세")
        self.assertEqual(len(detail.calls), 1)
        self.assertEqual(result["match_results"][0]["error"]["code"], "FIT_ERROR")
        self.assertTrue(any(p["error_code"] == "FIT_ERROR" for p in self.payloads("error")))

    def test_raw_secrets_and_personal_data_are_removed_before_any_subscriber(self):
        publisher = EventPublisher(self.bus, "privacy")
        publisher.emit(
            "tool_requested", tool="search_jobs", stage="execute",
            request="홍길동 secret-key", profile={"phone": "010-1234-5678"},
            api_key="secret-key", token="secret-token", reason="secret-reason",
            argument_fields=["keyword", "limit", "authorization"],
            arguments={"keyword": "홍길동 secret-key", "region": "secret-token", "limit": 1, "headers": {"authorization": "secret-key"}},
            response={"body": "secret-token"}, error_message="secret-error",
            reason_code="tool_request",
        )
        raw = self.events[0].payload
        self.assertNotIn("secret", raw)
        self.assertNotIn("홍길동", raw)
        self.assertNotIn("010-1234-5678", raw)
        payload = json.loads(raw)
        self.assertEqual(payload["arguments"], {"limit": 1})
        self.assertEqual(payload["argument_fields"], ["keyword", "limit"])

    def test_immutable_events_cannot_be_changed_by_a_subscriber(self):
        bus = EventBus()
        def modifies(event):
            event.payload = "changed"
        received = []
        bus.subscribe(modifies)
        bus.subscribe(received.append)
        EventPublisher(bus, "immutable").emit("run_started")
        self.assertEqual(json.loads(received[0].payload), {"seq": 1})
        self.assertEqual(bus.failure_count, 1)

    def test_stage_and_run_failures_close_with_safe_error_events(self):
        model = FakeModel()
        model.analyze = lambda messages: (_ for _ in ()).throw(RuntimeError("secret-llm-error"))
        with self.assertRaises(RuntimeError):
            self.session(model).invoke("failed-run", "검색")
        self.assertEqual(self.payloads("stage_finished")[-1]["status"], "failed")
        self.assertEqual(self.payloads("run_finished")[-1]["status"], "failed")
        self.assertNotIn("secret-llm-error", "".join(e.payload for e in self.events))

    def test_greeting_has_no_tool_calls(self):
        model = FakeModel([{"intents": [], "decision_reason": "general_chat"}])
        result = self.session(model).invoke("hello", "안녕")
        self.assert_complete(result)
        self.assertEqual(self.payloads("tool_requested"), [])
        self.assertEqual(self.payloads("intent_decided")[0]["reason_code"], "general_chat")

    def test_concurrent_run_contexts_stay_separate(self):
        def run(session_id):
            return self.session().invoke(session_id, "검색")
        with patch.dict(agent.TOOLS, search_jobs=FakeTool(lambda args: found([job()]))):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(run, ["parallel-a", "parallel-b"]))
        for result in results:
            events = [e for e in self.events if e.run_id == result["run_id"]]
            self.assertTrue(events)
            self.assertEqual({e.session_id for e in events}, {result["session_id"]})
            self.assertEqual([json.loads(e.payload)["seq"] for e in events], list(range(1, len(events) + 1)))
        self.assertEqual(Counter(e.event_type for e in self.events)["run_finished"], 2)


if __name__ == "__main__":
    unittest.main()
