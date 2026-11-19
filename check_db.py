import json
from contextlib import closing

from backend.app.db.database import connect_db

with closing(connect_db()) as db:
    try:
        skills = ["사례관리", "프로그램 기획"]
        profile = db.execute(
            "INSERT INTO user_profile (desired_role, skills) VALUES (?, ?)",
            ("사회복지사", json.dumps(skills, ensure_ascii=False)),
        )
        job = db.execute(
            """
            INSERT INTO job_posting
                (source, board_id, source_job_id, company, title, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("test", "test", "db-smoke-1", "테스트 기관", "사회복지사 채용", "2026-11-19T04:00:00Z"),
        )

        profile_row = db.execute(
            "SELECT skills FROM user_profile WHERE id = ?", (profile.lastrowid,)
        ).fetchone()
        job_row = db.execute(
            "SELECT deadline, job_url FROM job_posting WHERE id = ?", (job.lastrowid,)
        ).fetchone()

        assert json.loads(profile_row["skills"]) == skills
        assert job_row["deadline"] is None and job_row["job_url"] is None
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        print("DB 확인 완료")
    finally:
        db.rollback()