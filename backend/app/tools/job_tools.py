import json
import sqlite3
from contextlib import closing

from langchain_core.tools import tool

from backend.app.db.database import connect_db
from backend.app.db.job_search import search_jobs as search_cached_jobs
from backend.app.integrations import alio


def _failure(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}


@tool
def search_jobs(
    keyword: str = "",
    region: str = "",
    employment_type: str = "",
    ncs_category: str = "",
    career_type: str = "",
    ongoing_only: bool = True,
    limit: int = 20,
) -> dict:
    """키워드, 지역, 고용형태, NCS 분류, 경력으로 공고를 찾고 상세 조회에 쓸 DB id를 반환한다."""
    if limit < 1:
        return _failure("INVALID_INPUT", "limit은 1 이상이어야 합니다.")

    try:
        jobs = search_cached_jobs(
            keyword=keyword,
            region=region,
            employment_type=employment_type,
            ncs_category=ncs_category,
            career_type=career_type,
            ongoing_only=ongoing_only,
            limit=limit,
        )
    except (sqlite3.Error, json.JSONDecodeError):
        return _failure("DATABASE_ERROR", "저장된 공고를 읽지 못했습니다.")

    if not jobs:
        return _failure("NO_RESULT", "검색 조건에 맞는 공고가 없습니다.")

    return {"ok": True, "data": {"jobs": jobs, "count": len(jobs)}}


@tool
def get_job_detail(job_id: int) -> dict:
    """search_jobs에서 받은 DB id로 공고 상세와 필수·우대 자격을 조회한다."""
    if job_id < 1:
        return _failure("INVALID_INPUT", "공고 DB id는 1 이상이어야 합니다.")

    try:
        with closing(connect_db()) as db:
            row = db.execute(
                """
                SELECT id, source, board_id, source_job_id, company, title,
                       deadline, job_url, description_html,
                       required_qualifications, preferred_qualifications
                FROM job_posting
                WHERE id = ?
                """,
                (job_id,),
            ).fetchone()

        if row is None:
            return _failure("NO_RESULT", "해당 공고가 없습니다.")

        job = dict(row)
        fields = ("required_qualifications", "preferred_qualifications")
        for field in fields:
            if job[field] is not None:
                job[field] = json.loads(job[field])

        if job["source"] == "alio" and all(job[field] is None for field in fields):
            detail = alio.fetch_job_detail(job["source_job_id"])
            job["required_qualifications"] = alio._qualifications(detail, "aplyQlfcCn")
            job["preferred_qualifications"] = alio._qualifications(detail, "prefCondCn")

            values = [
                json.dumps(job[field], ensure_ascii=False)
                if job[field] is not None
                else None
                for field in fields
            ]

            with closing(connect_db()) as db, db:
                db.execute(
                    """
                    UPDATE job_posting
                    SET required_qualifications = COALESCE(required_qualifications, ?),
                        preferred_qualifications = COALESCE(preferred_qualifications, ?)
                    WHERE id = ?
                    """,
                    (*values, job_id),
                )

    except alio.AlioError as error:
        return _failure(error.code, str(error))
    except (sqlite3.Error, json.JSONDecodeError):
        return _failure("DATABASE_ERROR", "공고 상세 정보를 처리하지 못했습니다.")

    return {"ok": True, "data": job}


if __name__ == "__main__":
    from dotenv import load_dotenv

    from backend.app.db.database import PROJECT_ROOT

    load_dotenv(PROJECT_ROOT / ".env")

    result = search_jobs.invoke(
        {"keyword": "약사", "region": "강원", "employment_type": "정규직"}
    )
    if not result["ok"]:
        raise SystemExit(result["error"])

    print(f"search_jobs 성공 | {result['data']['count']}건")

    job = result["data"]["jobs"][0]
    detail = get_job_detail.invoke({"job_id": job["id"]})
    if not detail["ok"]:
        raise SystemExit(detail["error"])

    print(f"get_job_detail 성공 | {detail['data']['title']}")
    print("필수 자격 필드:", detail["data"]["required_qualifications"] is not None)