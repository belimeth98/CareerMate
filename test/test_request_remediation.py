import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.app import career_agent as agent
from backend.app.chat_api import get_chat_service
from backend.app.chat_service import ChatService
from backend.app.db.database import SCHEMA_PATH
from backend.app.db.job_search import KST
from backend.app.execution_events import EventBus
from backend.app.main import app
from backend.app.request_planning import make_schedule_draft, reference_from_text
from backend.app.workspace_api import get_workspace_service
from backend.app.workspace_service import WorkspaceService
from test_execution_logging import FakeModel, FakeTool, found, job


class RemediationGraphTests(unittest.TestCase):
    def invoke(self, analysis, request="검증 요청"):
        events=[];bus=EventBus();bus.subscribe(events.append)
        model=FakeModel([analysis]);session=agent.CareerSession(agent.build_graph(model),event_bus=bus)
        tool=FakeTool(lambda args:found([job()]))
        with patch.dict(agent.TOOLS,search_jobs=tool):
            result=session.invoke("remediation",request)
        return result,tool,model,events

    def test_past_basis_is_blocked_even_if_model_mislabels_deadline(self):
        state,tool,model,events=self.invoke({"intents":["search_jobs"],"deadline_on_or_after":"2026-10-01"},
            "2026년 10월 1일 기준으로 진행 중이었던 약사 공고를 찾아줘")
        self.assertEqual(state["reference_status"],"unsupported_reference_date")
        self.assertEqual(state["analysis"]["reference_date"],"2026-10-01")
        self.assertNotIn("deadline_on_or_after",state["analysis"])
        self.assertEqual(tool.calls,[])
        self.assertEqual(model.responses,0)
        self.assertIn("확인할 수 없습니다",state["answer"])
        self.assertNotIn("eligible_jobs",state)
        payloads=[json.loads(e.payload) for e in events if e.event_type=="constraint_decided"]
        self.assertEqual(payloads[0]["deadline_status"],"not_evaluated")
        self.assertEqual(payloads[0]["reason_code"],"unsupported_reference_date")

    def test_future_and_unspecified_past_basis_are_not_predicted(self):
        for data in ({"reference_date":"2099-01-01"},{"reference_query":True}):
            state,tool,_,_=self.invoke({"intents":["search_jobs"],**data})
            self.assertEqual(tool.calls,[])
            self.assertEqual(state["reference_status"],"unsupported_reference_date")

    def test_today_basis_is_supported(self):
        today=datetime.now(KST).date().isoformat()
        state,tool,_,_=self.invoke({"intents":["search_jobs"],"reference_date":today},"오늘 기준으로 약사 공고 찾아줘")
        self.assertEqual(len(tool.calls),1)
        self.assertEqual(state["search_summary"]["search_status"],"ok")

    def test_minimum_deadline_is_not_a_historical_request(self):
        state,tool,_,_=self.invoke({"intents":["search_jobs"],"deadline_on_or_after":"2099-01-01"},"마감일이 2099년 1월 1일 이후인 공고")
        self.assertEqual(state["reference_status"],"current")
        self.assertTrue(tool.calls)
        self.assertEqual(state["search_summary"]["deadline_status"],"unmet")

    def test_explicit_deadline_is_kept_with_a_separate_basis(self):
        state,tool,_,_=self.invoke({"intents":["search_jobs"],"deadline_on_or_after":"2026-12-01"},
                                 "2026년 10월 1일 기준 공고 중 마감일은 12월 1일 이후")
        self.assertEqual(state["analysis"]["deadline_on_or_after"],"2026-12-01")
        self.assertEqual(tool.calls,[])

    def test_deadline_basis_word_does_not_force_an_as_of_query(self):
        self.assertEqual(reference_from_text("마감일은 2026년 11월 30일 기준 이후 공고",datetime.now(KST).date()),(False,None))

    def test_complete_schedule_is_reviewable_and_uses_kst(self):
        state,tool,model,_=self.invoke({"intents":[],"schedule":{"title":"지원서 작성","start_date":"2026-11-21","start_time":"14:00","end_time":"15:00"}})
        self.assertEqual(tool.calls,[])
        self.assertEqual(model.responses,0)
        self.assertEqual(state["plan_draft"]["start_at"],"2026-11-21T14:00:00+09:00")
        self.assertEqual(state["plan_draft"]["end_at"],"2026-11-21T15:00:00+09:00")
        self.assertIn("아직 저장하지 않았습니다",state["answer"])

    def test_missing_end_is_requested_not_invented(self):
        state,_,_,_=self.invoke({"intents":[],"schedule":{"title":"지원서 작성","start_date":"2026-11-21","start_time":"14:00"}})
        self.assertIsNone(state["plan_draft"])
        self.assertIn("종료 시각",state["missing_inputs"])

    def test_date_only_draft(self):
        draft,missing=make_schedule_draft({"schedule":{"title":"접수 마감","due_date":"2026-11-30"}})
        self.assertEqual(missing,[])
        self.assertIsNone(draft["start_at"])
        self.assertEqual(draft["due_date"],"2026-11-30")

    def test_overnight_requires_explicit_end_date(self):
        fields={"title":"야간 준비","start_date":"2026-11-21","start_time":"23:00","end_time":"01:00"}
        draft,missing=make_schedule_draft({"schedule":fields})
        self.assertIsNone(draft);self.assertTrue(missing)
        draft,missing=make_schedule_draft({"schedule":{**fields,"end_date":"2026-11-22"}})
        self.assertEqual(missing,[])
        self.assertTrue(draft["end_at"].startswith("2026-11-22"))

    def test_schedule_followup_keeps_date_title_and_memory(self):
        model=FakeModel([
            {"intents":[],"schedule":{"title":"지원서 작성","start_date":"2026-11-21","start_time":"14:00","end_time":"15:00"}},
            {"intents":[],"is_follow_up":True,"schedule":{"start_time":"15:00","end_time":"16:00"}},
        ])
        session=agent.CareerSession(agent.build_graph(model))
        session.invoke("memory","일정 생성")
        result=session.invoke("memory","한 시간 늦춰줘")
        self.assertTrue(result["used_previous_context"])
        self.assertEqual(result["plan_draft"]["title"],"지원서 작성")
        self.assertEqual(result["plan_draft"]["start_at"],"2026-11-21T15:00:00+09:00")

    def test_schedule_after_search_does_not_repeat_previous_tools(self):
        model=FakeModel([
            {"intents":["search_jobs"],"limit":1},
            {"intents":[],"is_follow_up":True,"schedule":{"title":"지원 준비","due_date":"2026-11-30"}},
        ])
        session=agent.CareerSession(agent.build_graph(model));tool=FakeTool(lambda args:found([job()]))
        with patch.dict(agent.TOOLS,search_jobs=tool):
            session.invoke("switch","검색")
            result=session.invoke("switch","지원 일정도 만들어줘")
        self.assertEqual(len(tool.calls),1)
        self.assertTrue(result["plan_draft"])

    def test_missing_end_can_be_completed_in_the_same_session(self):
        model=FakeModel([
            {"intents":[],"schedule":{"title":"지원서","start_date":"2026-11-21","start_time":"14:00"}},
            {"intents":[],"is_follow_up":True,"schedule":{"end_time":"15:00"}},
        ])
        session=agent.CareerSession(agent.build_graph(model))
        self.assertIsNone(session.invoke("missing-end","일정 요청")["plan_draft"])
        result=session.invoke("missing-end","종료는 3시로 해줘")
        self.assertEqual(result["plan_draft"]["title"],"지원서")
        self.assertEqual(result["plan_draft"]["end_at"],"2026-11-21T15:00:00+09:00")

    def test_schedule_request_clears_previous_unsupported_reference(self):
        model=FakeModel([
            {"intents":["search_jobs"],"reference_query":True,"reference_date":"2026-10-01"},
            {"intents":[],"is_follow_up":True,"schedule":{"title":"지원서","due_date":"2026-11-21"}},
        ])
        session=agent.CareerSession(agent.build_graph(model))
        session.invoke("switch-reference","과거 기준 공고")
        result=session.invoke("switch-reference","그럼 지원 일정만 만들어줘")
        self.assertEqual(result["reference_status"],"current")
        self.assertIsNotNone(result["plan_draft"])

    def test_mixed_search_and_schedule_still_executes_search(self):
        state,tool,_,_=self.invoke({"intents":["search_jobs"],"schedule":{"title":"준비","due_date":"2026-11-30"}})
        self.assertEqual(len(tool.calls),1)
        self.assertTrue(state["eligible_jobs"])
        self.assertTrue(state["plan_draft"])

    def test_schedule_content_is_not_published_in_events(self):
        _,_,_,events=self.invoke({"intents":[],"schedule":{"title":"private-name","notes":"private-phone","due_date":"2026-11-30"}})
        self.assertNotIn("private-","".join(e.payload for e in events))


class RemediationAPITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/"test.sqlite3"
        with closing(self.connect()) as db:db.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.workspace=WorkspaceService(self.connect)
        self.model=FakeModel([{"intents":[],"schedule":{"title":"지원서 작성","start_date":"2026-11-21","start_time":"14:00","end_time":"15:00"}}])
        self.chat=ChatService(model_factory=lambda:self.model,workspace=self.workspace,bus=EventBus())
        app.dependency_overrides[get_chat_service]=lambda:self.chat
        app.dependency_overrides[get_workspace_service]=lambda:self.workspace
        self.addCleanup(app.dependency_overrides.clear)
        self.client=TestClient(app);self.client.__enter__();self.addCleanup(self.client.__exit__,None,None,None)

    def connect(self):
        db=sqlite3.connect(self.path);db.row_factory=sqlite3.Row;db.execute("PRAGMA foreign_keys=ON");return db

    def test_draft_not_saved_until_explicit_create_and_conflicts_still_reported(self):
        profile=self.client.post("/api/profiles",json={"desired_role":"개발자"}).json()
        result=self.client.post("/api/chat",json={"message":"내일 14~15시 지원서 작성","profile_id":profile["id"]})
        self.assertEqual(result.status_code,200,result.text)
        draft=result.json()["plan_draft"]
        self.assertEqual(self.client.get(f"/api/plans?profile_id={profile['id']}").json()["plans"],[])
        body={**draft,"user_profile_id":profile["id"]}
        first=self.client.post("/api/plans",json=body)
        self.assertEqual(first.status_code,201,first.text)
        self.assertTrue(first.json()["plan"]["start_at"].startswith("2026-11-21T05:00"))
        second=self.client.post("/api/plans",json={**body,"title":"겹치는 준비"})
        self.assertEqual(second.status_code,201)
        self.assertEqual(len(second.json()["conflicts"]),1)

    def test_unsupported_reference_is_explicit_in_api(self):
        self.model=FakeModel([{"intents":["search_jobs"],"deadline_on_or_after":"2026-10-01"}])
        result=self.client.post("/api/chat",json={"message":"2026년 10월 1일 기준 진행 공고"})
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json()["reference_status"],"unsupported_reference_date")
        self.assertEqual(result.json()["jobs"],[])
        self.assertEqual(result.json()["tool_calls"],[])


if __name__=="__main__":unittest.main()
