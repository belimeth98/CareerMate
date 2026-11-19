"""External candidate Tool checks with fake upstream data and a temporary DB."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from backend.app.db import database
from backend.app.integrations import alio
from backend.app.tools import job_tools


def posting(source_id, title="약사 채용"):
    return alio.normalize_job({
        "recrutPblntSn": source_id, "instNm": "테스트기관", "recrutPbancTtl": title,
        "workRgnNmLst": "강원", "hireTypeNmLst": "정규직", "ongoingYn": "Y",
        "pbancEndYmd": "20991231",
    })


class CandidateRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "jobs.sqlite3"
        patcher = patch.object(database, "DB_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        database.init_db()
        with closing(database.connect_db()) as db, db:
            self.old_id = alio.upsert_job(db, posting("old"))

    def test_default_calls_external_collector_and_returns_detail_identifiers(self):
        with patch.object(job_tools, "collect_open_jobs", return_value=[posting("old"), posting("new")]) as fetch:
            result = job_tools.search_jobs.invoke({"keyword": "약사"})
        fetch.assert_called_once_with(quiet=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["data_source"], "external_api")
        self.assertEqual(result["data"]["count"], 2)
        self.assertIn(self.old_id, [job["id"] for job in result["data"]["jobs"]])
        self.assertTrue(all(job["title"] for job in result["data"]["jobs"]))

    def test_replan_cache_mode_does_not_call_external_api(self):
        with patch.object(job_tools, "collect_open_jobs") as fetch:
            result = job_tools.search_jobs.invoke({"refresh": False})
        fetch.assert_not_called()
        self.assertEqual(result["data"]["data_source"], "cache")

    def test_external_failure_is_an_error_and_preserves_existing_cache(self):
        with patch.object(job_tools, "collect_open_jobs", side_effect=alio.AlioError("UPSTREAM_TIMEOUT", "응답 시간 초과")):
            result = job_tools.search_jobs.invoke({})
        self.assertEqual(result["error"]["code"], "UPSTREAM_TIMEOUT")
        with closing(database.connect_db()) as db:
            self.assertEqual(db.execute("SELECT is_open FROM job_posting WHERE id=?", (self.old_id,)).fetchone()[0], 1)

    def test_empty_external_snapshot_closes_stale_jobs_and_returns_normal_zero(self):
        with patch.object(job_tools, "collect_open_jobs", return_value=[]):
            result = job_tools.search_jobs.invoke({})
        self.assertEqual(result["error"]["code"], "NO_RESULT")
        with closing(database.connect_db()) as db:
            self.assertEqual(db.execute("SELECT is_open FROM job_posting WHERE id=?", (self.old_id,)).fetchone()[0], 0)

    def test_mid_transaction_failure_rolls_back_changes(self):
        with patch.object(job_tools, "collect_open_jobs", return_value=[posting("new")]):
            with patch.object(alio, "upsert_job", side_effect=sqlite3.OperationalError("private database details")):
                result = job_tools.search_jobs.invoke({})
        self.assertEqual(result["error"]["code"], "DATABASE_ERROR")
        self.assertNotIn("private", result["error"]["message"])
        with closing(database.connect_db()) as db:
            self.assertEqual(db.execute("SELECT is_open FROM job_posting WHERE id=?", (self.old_id,)).fetchone()[0], 1)

    def test_malformed_external_data_does_not_close_existing_jobs(self):
        with patch.object(job_tools, "collect_open_jobs", return_value=[{}]):
            result = job_tools.search_jobs.invoke({})
        self.assertEqual(result["error"]["code"], "UPSTREAM_INVALID_RESPONSE")
        with closing(database.connect_db()) as db:
            self.assertEqual(db.execute("SELECT is_open FROM job_posting WHERE id=?", (self.old_id,)).fetchone()[0], 1)

    def test_refresh_keeps_other_sources_and_cached_qualifications(self):
        other = posting("other")
        other.update(source="lever", board_id="test")
        with closing(database.connect_db()) as db, db:
            alio.upsert_job(db, other)
            db.execute("UPDATE job_posting SET required_qualifications='[\"약사 면허\"]' WHERE id=?", (self.old_id,))
        with patch.object(job_tools, "collect_open_jobs", return_value=[posting("old")]):
            result = job_tools.search_jobs.invoke({})
        self.assertTrue(result["ok"])
        with closing(database.connect_db()) as db:
            self.assertEqual(db.execute("SELECT is_open FROM job_posting WHERE source='lever'").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT required_qualifications FROM job_posting WHERE id=?", (self.old_id,)).fetchone()[0], '["약사 면허"]')


if __name__ == "__main__":
    unittest.main()
