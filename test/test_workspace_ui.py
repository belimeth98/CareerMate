import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import career_agent
from backend.app.chat_api import get_chat_service
from backend.app.chat_service import ChatService
from backend.app.db.database import SCHEMA_PATH
from backend.app.execution_events import EventBus
from backend.app.job_fit import JobCriteria, evaluate_match
from backend.app.logging_agent import LoggingAgent
from backend.app.main import app
from backend.app.workspace_api import get_workspace_service
from backend.app.workspace_service import WorkspaceService
from backend.app.workspace_schemas import PlanInput
from test_execution_logging import FakeModel, FakeTool, found, job


class WorkspaceUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "ui.sqlite3"
        with closing(self.connect()) as db:
            db.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            db.execute("""INSERT INTO job_posting (id,source,board_id,source_job_id,company,title,
                         location,employment_type,deadline,is_open,fetched_at)
                         VALUES (1,'alio','alio','test','기관','백엔드 개발자','["서울"]','["정규직"]','2099-12-31',1,'2026-11-19')""")
            db.commit()
        self.workspace = WorkspaceService(self.connect)
        app.dependency_overrides[get_workspace_service] = lambda: self.workspace
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app, raise_server_exceptions=False)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.profile_id = self.client.post("/api/profiles", json={"desired_role":"개발자","preferred_locations":["서울"]}).json()["id"]
        self.other_id = self.client.post("/api/profiles", json={"desired_role":"약사"}).json()["id"]

    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def plan_body(self, **changes):
        data = {"user_profile_id":self.profile_id,"title":"지원서 준비","plan_type":"application",
                "start_at":"2026-11-20T10:00:00+09:00","end_at":"2026-11-20T11:00:00+09:00"}
        return {**data, **changes}

    def create(self, **changes):
        response = self.client.post("/api/plans", json=self.plan_body(**changes))
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_root_and_static_files_are_served(self):
        response=self.client.get("/")
        self.assertEqual(response.status_code,200)
        self.assertIn("CareerMate",response.text)
        for path in ("/assets/style.css","/assets/app.js"):
            self.assertEqual(self.client.get(path).status_code,200)

    def test_profile_roundtrip_and_normalized_lists(self):
        body={"desired_role":" 약사 ","skills":["Python"," Python ",""],"certifications":["약사 면허"]}
        updated=self.client.put(f"/api/profiles/{self.profile_id}",json=body)
        self.assertEqual(updated.status_code,200)
        self.assertEqual(updated.json()["skills"],["Python"])
        self.assertEqual(self.client.get(f"/api/profiles/{self.profile_id}").json()["desired_role"],"약사")
        self.assertEqual(len(self.client.get("/api/profiles").json()["profiles"]),2)

    def test_timed_plan_is_utc_and_has_creation_time(self):
        saved=self.create()
        self.assertTrue(saved["plan"]["start_at"].startswith("2026-11-20T01:00"))
        self.assertIsNotNone(saved["plan"]["created_at"])
        self.assertEqual(saved["conflicts"],[])

    def test_conflict_is_saved_with_list_and_warning(self):
        first=self.create()["plan"]
        second=self.create(title="포트폴리오 준비",start_at="2026-11-20T10:30:00+09:00")
        self.assertEqual([p["id"] for p in second["conflicts"]],[first["id"]])
        self.assertIn("저장",second["warning"])
        self.assertEqual(len(self.client.get(f"/api/plans?profile_id={self.profile_id}").json()["plans"]),2)

    def test_adjacent_times_do_not_conflict(self):
        self.create()
        self.assertEqual(self.create(start_at="2026-11-20T11:00:00+09:00",end_at="2026-11-20T12:00:00+09:00")["conflicts"],[])

    def test_other_profile_and_cancelled_plans_do_not_conflict(self):
        self.create(user_profile_id=self.other_id)
        self.create(status="cancelled")
        self.assertIsNone(self.create()["warning"])

    def test_cross_midnight_overlap_is_detected(self):
        self.create(start_at="2026-11-20T23:30:00+09:00",end_at="2026-11-21T01:00:00+09:00")
        self.assertEqual(len(self.create(start_at="2026-11-21T00:30:00+09:00",end_at="2026-11-21T02:00:00+09:00")["conflicts"]),1)

    def test_patch_excludes_itself_and_preserves_creation_time(self):
        original=self.create()["plan"]
        result=self.client.patch(f"/api/plans/{original['id']}?profile_id={self.profile_id}",json={"title":"수정된 계획"})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()["conflicts"],[])
        self.assertEqual(result.json()["plan"]["created_at"],original["created_at"])
        self.assertEqual(result.json()["plan"]["title"],"수정된 계획")

    def test_patch_conflict_and_invalid_patch_rollback(self):
        first=self.create()["plan"]
        second=self.create(start_at="2026-11-20T12:00:00+09:00",end_at="2026-11-20T13:00:00+09:00")["plan"]
        url=f"/api/plans/{second['id']}?profile_id={self.profile_id}"
        result=self.client.patch(url,json={"start_at":"2026-11-20T10:30:00+09:00"})
        self.assertEqual(result.json()["conflicts"][0]["id"],first["id"])
        self.assertEqual(self.client.patch(url,json={"end_at":"2026-11-19T01:00:00+09:00"}).status_code,422)
        self.assertEqual(self.client.get(url).json()["end_at"],second["end_at"])

    def test_date_only_plan_and_explicit_null_patch(self):
        original=self.create()["plan"]
        url=f"/api/plans/{original['id']}?profile_id={self.profile_id}"
        result=self.client.patch(url,json={"start_at":None,"end_at":None,"due_date":"2026-11-30"})
        self.assertEqual(result.status_code,200)
        self.assertIsNone(result.json()["plan"]["start_at"])
        self.assertEqual(result.json()["plan"]["due_date"],"2026-11-30")
        self.assertEqual(self.client.patch(url,json={"due_date":None}).status_code,422)

    def test_invalid_schedule_and_naive_time_rejected(self):
        for changes in ({"end_at":None},{"end_at":"2026-11-20T09:00:00+09:00"},{"start_at":"2026-11-20T10:00:00"},{"title":" "}):
            with self.subTest(changes=changes):
                self.assertEqual(self.client.post("/api/plans",json=self.plan_body(**changes)).status_code,422)

    def test_plan_ownership_and_delete(self):
        plan=self.create()["plan"]
        wrong=f"/api/plans/{plan['id']}?profile_id={self.other_id}"
        self.assertEqual(self.client.get(wrong).status_code,404)
        self.assertEqual(self.client.patch(wrong,json={"title":"wrong"}).status_code,404)
        self.assertEqual(self.client.delete(wrong).status_code,404)
        url=f"/api/plans/{plan['id']}?profile_id={self.profile_id}"
        self.assertEqual(self.client.delete(url).status_code,200)
        self.assertEqual(self.client.get(url).status_code,404)
        self.assertEqual(self.client.get(f"/api/plans?profile_id={self.profile_id}").json()["plans"],[])

    def test_missing_job_and_profile_are_rejected(self):
        self.assertEqual(self.client.post("/api/plans",json=self.plan_body(job_posting_id=999)).status_code,404)
        self.assertEqual(self.client.post("/api/plans",json=self.plan_body(user_profile_id=999)).status_code,404)

    def test_growth_ownership_and_linked_delete(self):
        growth=self.client.post("/api/growth-plans",json={"user_profile_id":self.profile_id,"plan_type":"skill","target_name":"Docker","target_date":"2026-11-21"}).json()
        self.assertEqual(self.client.post("/api/plans",json=self.plan_body(user_profile_id=self.other_id,growth_plan_id=growth["id"])).status_code,422)
        plan=self.create(growth_plan_id=growth["id"])["plan"]
        url=f"/api/growth-plans/{growth['id']}?profile_id={self.profile_id}"
        self.assertEqual(self.client.delete(url).status_code,409)
        self.client.delete(f"/api/plans/{plan['id']}?profile_id={self.profile_id}")
        self.assertEqual(self.client.delete(url).status_code,200)

    def test_growth_changes_future_not_current_score(self):
        self.client.post("/api/growth-plans",json={"user_profile_id":self.profile_id,"plan_type":"skill","target_name":"Docker","expected_skills":["Docker"],"target_date":"2026-11-21"})
        with closing(self.connect()) as db:
            fit=evaluate_match(db,self.profile_id,1,JobCriteria(required_skills=("Docker",),requirements_checked=True),date(2026,11,19))
        self.assertGreater(fit["future"]["score"],fit["current"]["score"])
        self.assertIn("Docker",fit["applied_plans"])

    def test_simultaneous_overlapping_saves_detect_conflict(self):
        data=PlanInput.model_validate(self.plan_body())
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:self.workspace.save_plan(data),range(2)))
        self.assertEqual(sum(len(r["conflicts"]) for r in results),1)

    def configure_chat(self, model=None, bus=None):
        if bus is None:
            bus=EventBus();bus.subscribe(LoggingAgent(self.connect))
        service=ChatService(model_factory=lambda:model or FakeModel(),workspace=self.workspace,bus=bus)
        app.dependency_overrides[get_chat_service]=lambda:service
        return service

    def test_chat_followup_keeps_conditions_and_run_ids_differ(self):
        model=FakeModel([
            {"intents":["search_jobs"],"keyword":"약사","region":"강원","employment_type":"정규직","limit":1},
            {"region":"서울","is_follow_up":True},
        ])
        self.configure_chat(model)
        with patch.dict(career_agent.TOOLS,search_jobs=FakeTool(lambda args:found([job()]))):
            first=self.client.post("/api/chat",json={"message":"첫 요청","session_id":"ui-memory","assess_fit":False})
            second=self.client.post("/api/chat",json={"message":"서울로 변경","session_id":"ui-memory","assess_fit":False})
        self.assertEqual(first.status_code,200,first.text)
        self.assertEqual(second.status_code,200,second.text)
        self.assertNotEqual(first.json()["run_id"],second.json()["run_id"])
        self.assertTrue(second.json()["used_previous_context"])
        self.assertEqual(second.json()["analysis"]["keyword"],"약사")
        self.assertEqual(second.json()["events"][-1]["event_type"],"run_finished")

    def test_chat_no_result_and_tool_failure_are_distinct(self):
        for code in ("NO_RESULT","UPSTREAM_ERROR","MISSING_API_KEY"):
            self.configure_chat()
            with patch.dict(career_agent.TOOLS,search_jobs=FakeTool(lambda args:{"ok":False,"error":{"code":code,"message":"private details"}})):
                response=self.client.post("/api/chat",json={"message":"검색","assess_fit":False})
            self.assertEqual(response.status_code,200,response.text)
            expected="no_result" if code=="NO_RESULT" else "error"
            self.assertEqual(response.json()["tool_calls"][0]["status"],expected)
            self.assertNotIn("private details",json.dumps(response.json()["events"]))

    def test_missing_gms_key_does_not_break_plans_or_health(self):
        service=self.configure_chat()
        service.model_factory=lambda:(_ for _ in ()).throw(RuntimeError("missing"))
        response=self.client.post("/api/chat",json={"message":"안녕"})
        self.assertEqual(response.status_code,503)
        self.assertEqual(response.json()["error"]["code"],"MISSING_API_KEY")
        self.assertEqual(self.client.get("/health").status_code,200)
        self.assertEqual(self.client.get(f"/api/plans?profile_id={self.profile_id}").status_code,200)

    def test_agent_failure_returns_safe_events(self):
        model=FakeModel()
        model.analyze=lambda messages:(_ for _ in ()).throw(RuntimeError("secret-key-and-profile"))
        self.configure_chat(model)
        response=self.client.post("/api/chat",json={"message":"검색"})
        self.assertEqual(response.status_code,502,response.text)
        self.assertEqual(response.json()["error"]["code"],"AGENT_ERROR")
        self.assertTrue(response.json()["events"])
        self.assertNotIn("secret-key-and-profile",response.text)

    def test_logging_failure_still_returns_chat_events(self):
        bus=EventBus()
        bus.subscribe(lambda event:(_ for _ in ()).throw(sqlite3.OperationalError("private")))
        self.configure_chat(bus=bus)
        with patch.dict(career_agent.TOOLS,search_jobs=FakeTool(lambda args:found([job()]))):
            response=self.client.post("/api/chat",json={"message":"검색","assess_fit":False})
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()["events"])
        self.assertGreater(bus.failure_count,0)

    def test_greeting_calls_no_tools(self):
        self.configure_chat(FakeModel([{"intents":[],"decision_reason":"general_chat"}]))
        response=self.client.post("/api/chat",json={"message":"안녕"})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()["tool_calls"],[])

    def test_busy_session_is_explicit_409(self):
        service=self.configure_chat()
        from threading import Lock
        service._locks["busy"]=Lock();service._locks["busy"].acquire()
        response=self.client.post("/api/chat",json={"message":"검색","session_id":"busy"})
        self.assertEqual(response.status_code,409)
        self.assertEqual(response.json()["error"]["code"],"SESSION_BUSY")

    def test_real_fit_tuple_is_serialized_as_json_array(self):
        self.configure_chat()
        fit={"current":{"score":65,"assessed_weight":65,"eligibility":"unknown"},
             "future":{"score":65,"assessed_weight":65,"eligibility":"unknown"},
             "applied_plans":[],"criteria":{"other_preferred":("취업지원대상자",)}}
        with patch.dict(career_agent.TOOLS,search_jobs=FakeTool(lambda args:found([job()])),
                        get_job_detail=FakeTool(lambda args:{"ok":True,"data":job()})):
            with patch.object(career_agent,"score_posting",return_value=fit):
                response=self.client.post("/api/chat",json={"message":"검색","profile_id":self.profile_id})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()["matches"][0]["other_preferred"],["취업지원대상자"])

    def test_response_validation_failure_does_not_expose_private_values(self):
        with patch.object(self.workspace,"save_profile",return_value={"private":"secret-profile"}):
            response=self.client.post("/api/profiles",json={"desired_role":"개발자"})
        self.assertEqual(response.status_code,500)
        self.assertEqual(response.json()["error"]["code"],"INVALID_RESPONSE")
        self.assertNotIn("secret-profile",response.text)


if __name__=="__main__":
    unittest.main()
