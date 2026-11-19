"""F3014 API tests. Every test uses a temporary SQLite file, never the user's DB."""

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

from fastapi.testclient import TestClient

from backend.app.db.database import SCHEMA_PATH
from backend.app.log_api import get_log_service
from backend.app.log_service import LogService
from backend.app.main import app


class LogAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "test.sqlite3"
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            db.executemany(
                "INSERT INTO execution_event(session_id, run_id, event_type, created_at, duration_ms, payload) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    ("session-a", "run-a", "run_started", "2026-11-19T10:00:00Z", None, '{"seq":1}'),
                    ("session-b", "run-b", "run_started", "2026-11-19T09:00:00Z", None, '{"seq":1}'),
                    ("session-a", "run-a", "tool_finished", "2026-11-19T08:00:00Z", 4, '{"seq":2,"status":"no_result","count":0}'),
                    ("session-a", "run-a", "run_finished", "2026-11-19T08:00:00Z", 6, '{"seq":3,"status":"completed"}'),
                    ("session-a", "run-a2", "error", "2026-11-19T08:00:00Z", None, '{"seq":1,"error_code":"TOOL_ERROR"}'),
                    ("session-b", "run-b", "run_finished", "2026-11-19T07:00:00Z", 7, '{"seq":2,"status":"completed"}'),
                ],
            )
            db.commit()
        self.service = LogService(lambda: sqlite3.connect(self.path))
        app.dependency_overrides[get_log_service] = lambda: self.service
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app, raise_server_exceptions=False)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_list_groups_sessions_and_orders_by_latest_persisted_event(self):
        response = self.client.get("/api/logs")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["total"], 2)
        self.assertEqual([item["session_id"] for item in data["sessions"]], ["session-b", "session-a"])
        summary = data["sessions"][1]
        self.assertEqual(summary["event_count"], 4)
        self.assertEqual(summary["run_count"], 2)
        self.assertEqual(summary["error_count"], 1)
        self.assertEqual(summary["first_event_at"], "2026-11-19T10:00:00Z")
        self.assertEqual(summary["last_event_at"], "2026-11-19T08:00:00Z")

    def test_detail_is_session_scoped_in_insertion_order_not_clock_order(self):
        response = self.client.get("/api/logs/session-a")
        self.assertEqual(response.status_code, 200)
        events = response.json()["events"]
        self.assertEqual([item["id"] for item in events], [1, 3, 4, 5])
        self.assertEqual({item["session_id"] for item in events}, {"session-a"})
        self.assertEqual(events[1]["duration_ms"], 4)
        self.assertEqual(events[1]["payload"], {"seq": 2, "status": "no_result", "count": 0})

    def test_detail_decodes_payload_without_changing_values(self):
        payload = {"message": "한글", "nested": {"items": [0, True, None, 1.5]}}
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE execution_event SET payload=? WHERE id=1", (json.dumps(payload, ensure_ascii=False),))
        self.assertEqual(self.client.get("/api/logs/session-a").json()["events"][0]["payload"], payload)

    def test_list_and_detail_pagination_have_stable_totals(self):
        data = self.client.get("/api/logs?limit=1&offset=1").json()
        self.assertEqual(data["total"], 2)
        self.assertEqual(data["sessions"][0]["session_id"], "session-a")
        data = self.client.get("/api/logs/session-a?limit=2&offset=1").json()
        self.assertEqual(data["total"], 4)
        self.assertEqual([item["id"] for item in data["events"]], [3, 4])
        self.assertEqual(self.client.get("/api/logs?offset=100").json()["sessions"], [])
        self.assertEqual(self.client.get("/api/logs/session-a?offset=100").json()["events"], [])

    def test_delete_removes_only_the_requested_session(self):
        response = self.client.delete("/api/logs/session-a")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"session_id": "session-a", "deleted_count": 4})
        self.assertEqual(self.client.get("/api/logs/session-a").status_code, 404)
        self.assertEqual(self.client.get("/api/logs/session-b").json()["total"], 2)
        self.assertEqual(self.client.get("/api/logs").json()["total"], 1)

    def test_missing_session_returns_explicit_404(self):
        for method in (self.client.get, self.client.delete):
            with self.subTest(method=method):
                response = method("/api/logs/absent")
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json()["error"]["code"], "LOG_SESSION_NOT_FOUND")

    def test_repeated_delete_is_404(self):
        self.assertEqual(self.client.delete("/api/logs/session-a").status_code, 200)
        self.assertEqual(self.client.delete("/api/logs/session-a").status_code, 404)

    def test_session_id_is_bound_as_sql_data(self):
        url = "/api/logs/" + quote("x' OR '1'='1", safe="")
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.delete(url).status_code, 404)
        self.assertEqual(self.client.get("/api/logs").json()["total"], 2)

    def test_invalid_pagination_returns_422_with_safe_response(self):
        for path in (
            "/api/logs?limit=0", "/api/logs?limit=201", "/api/logs?offset=-1",
            "/api/logs/session-a?limit=1001", "/api/logs/session-a?offset=-1",
            "/api/logs?limit=secret-token",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["error"]["code"], "INVALID_INPUT")
                self.assertNotIn("secret-token", response.text)

    def test_long_session_id_returns_422(self):
        self.assertEqual(self.client.get("/api/logs/" + "x" * 129).status_code, 422)

    def test_no_log_creation_or_modification_routes_exist(self):
        paths = self.client.get("/openapi.json").json()["paths"]
        self.assertEqual(set(paths["/api/logs"]), {"get"})
        self.assertEqual(set(paths["/api/logs/{session_id}"]), {"get", "delete"})
        for method in ("PATCH", "PUT", "POST"):
            response = self.client.request(method, "/api/logs/session-a", json={"payload": {"changed": True}})
            self.assertEqual(response.status_code, 405)
        self.assertEqual(self.client.get("/api/logs/session-a").json()["events"][0]["payload"], {"seq": 1})

    def test_corrupt_payload_is_a_safe_500_and_other_endpoints_stay_healthy(self):
        for payload in ("private-invalid-json", "[]", '{"value":NaN}'):
            with self.subTest(payload=payload):
                with closing(sqlite3.connect(self.path)) as db, db:
                    db.execute("UPDATE execution_event SET payload=? WHERE id=1", (payload,))
                response = self.client.get("/api/logs/session-a")
                self.assertEqual(response.status_code, 500)
                self.assertEqual(response.json()["error"]["code"], "LOG_PAYLOAD_ERROR")
                self.assertNotIn("private", response.text)
                self.assertEqual(self.client.get("/api/logs").status_code, 200)
                self.assertEqual(self.client.get("/health").json(), {"status": "ok"})

    def test_legacy_null_payload_is_preserved(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE execution_event SET payload=NULL WHERE id=1")
        response = self.client.get("/api/logs/session-a")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["events"][0]["payload"])

    def test_database_failure_is_a_safe_503(self):
        def unavailable():
            raise sqlite3.OperationalError("private SQL and filesystem path")
        self.service = LogService(unavailable)
        for method, path in (("GET", "/api/logs"), ("GET", "/api/logs/session-a"), ("DELETE", "/api/logs/session-a")):
            response = self.client.request(method, path)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["error"]["code"], "DATABASE_ERROR")
            self.assertNotIn("private", response.text)

    def test_empty_database_returns_an_empty_list(self):
        self.client.delete("/api/logs/session-a")
        self.client.delete("/api/logs/session-b")
        data = self.client.get("/api/logs").json()
        self.assertEqual(data["total"], 0)
        self.assertEqual(data["sessions"], [])

    def test_openapi_documents_success_and_error_models(self):
        spec = self.client.get("/openapi.json").json()
        detail = spec["paths"]["/api/logs/{session_id}"]["get"]["responses"]
        self.assertEqual(set(detail), {"200", "404", "422", "500", "503"})
        self.assertIn("LogSessionDetail", detail["200"]["content"]["application/json"]["schema"]["$ref"])
        self.assertIn("ErrorResponse", detail["404"]["content"]["application/json"]["schema"]["$ref"])

    def test_health_endpoint_is_preserved(self):
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
