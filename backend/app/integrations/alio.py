import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

import requests

BASE_URL = "https://opendata.alio.go.kr"


class AlioError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _request(endpoint: str, params: dict) -> dict:
    api_key = os.getenv("ALIO_API_KEY")
    if not api_key:
        raise AlioError("MISSING_API_KEY", "ALIO_API_KEY가 비어 있습니다.")

    try:
        response = requests.post(
            f"{BASE_URL}{endpoint}",
            params={**params, "serviceKey": api_key, "resultType": "json"},
            timeout=(5, 20),
            allow_redirects=False,
        )
    except requests.Timeout:
        raise AlioError("UPSTREAM_TIMEOUT", "잡알리오 응답 시간이 초과됐습니다.") from None
    except requests.RequestException:
        raise AlioError("UPSTREAM_ERROR", "잡알리오에 연결하지 못했습니다.") from None

    if response.status_code != 200:
        raise AlioError("UPSTREAM_ERROR", f"잡알리오 HTTP 상태 {response.status_code}")

    try:
        data = response.json()
    except ValueError:
        raise AlioError(
            "UPSTREAM_INVALID_RESPONSE", "잡알리오 응답을 읽을 수 없습니다."
        ) from None

    if not isinstance(data, dict):
        raise AlioError("UPSTREAM_INVALID_RESPONSE", "잡알리오 응답 형식이 올바르지 않습니다.")
    if str(data.get("resultCode")) not in {"0", "200"}:
        raise AlioError("UPSTREAM_ERROR", "잡알리오에서 요청을 처리하지 못했습니다.")

    return data


def fetch_jobs(page: int = 1, page_size: int = 50) -> list[dict]:
    data = _request(
        "/new/v1/recruit/list.do",
        {"pageNo": page, "numOfRows": page_size, "ongoingYn": "Y"},
    )
    jobs = data.get("result")

    if jobs in (None, {}):
        return []
    if isinstance(jobs, dict):
        return [jobs]
    if isinstance(jobs, list) and all(isinstance(job, dict) for job in jobs):
        return jobs

    raise AlioError("UPSTREAM_INVALID_RESPONSE", "공고 목록 형식이 올바르지 않습니다.")


def fetch_job_detail(source_job_id: str) -> dict:
    data = _request("/new/v1/recruit/detail.do", {"sn": source_job_id})
    detail = data.get("result")

    if detail in (None, [], {}):
        raise AlioError("NO_RESULT", f"공고 {source_job_id}의 상세 정보가 없습니다.")
    if isinstance(detail, list) and len(detail) == 1:
        detail = detail[0]
    if not isinstance(detail, dict) or not detail:
        raise AlioError("UPSTREAM_INVALID_RESPONSE", "공고 상세 형식이 올바르지 않습니다.")

    detail_id = detail.get("recrutPblntSn")
    if detail_id is not None and str(detail_id) != source_job_id:
        raise AlioError("UPSTREAM_INVALID_RESPONSE", "요청과 응답의 공고 번호가 다릅니다.")

    return detail


def _as_list(value: str | list[str] | None) -> list[str] | None:
    if value is None:
        return None

    items = value.split(",") if isinstance(value, str) else value
    return [item.strip() for item in items if isinstance(item, str) and item.strip()]


def _deadline(value: str | int | None) -> str | None:
    digits = str(value or "").strip().replace("-", "")
    if len(digits) != 8:
        return None

    try:
        return datetime.strptime(digits, "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def _qualifications(detail: dict | None, field: str) -> list[str] | None:
    if detail is None or field not in detail:
        return None

    value = detail[field]
    if value is None:
        return []

    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
        raise AlioError("UPSTREAM_INVALID_RESPONSE", "자격조건 형식이 올바르지 않습니다.")

    return [item.strip() for item in items if item.strip() not in {"", "없음", "없음."}]


def normalize_job(raw: dict, detail: dict | None = None) -> dict:
    source_job_id = raw.get("recrutPblntSn")
    company = (raw.get("instNm") or "").strip()
    title = (raw.get("recrutPbancTtl") or "").strip()

    if source_job_id is None or not company or not title:
        raise AlioError(
            "UPSTREAM_INVALID_RESPONSE", "공고 번호, 기관명 또는 제목이 빠져 있습니다."
        )

    url = (raw.get("srcUrl") or "").strip()
    return {
        "source": "alio",
        "board_id": "alio",
        "source_job_id": str(source_job_id),
        "company": company,
        "title": title,
        "location": _as_list(raw.get("workRgnNmLst")),
        "employment_type": _as_list(raw.get("hireTypeNmLst")),
        "career_type": _as_list(raw.get("recrutSeNm")),
        "ncs_categories": _as_list(raw.get("ncsCdNmLst")),
        "deadline": _deadline(raw.get("pbancEndYmd")),
        "is_open": {"Y": 1, "N": 0}.get(raw.get("ongoingYn")),
        "job_url": None if url in {"", "없음", "없음."} else url,
        "required_qualifications": _qualifications(detail, "aplyQlfcCn"),
        "preferred_qualifications": _qualifications(detail, "prefCondCn"),
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def upsert_job(db: sqlite3.Connection, job: dict) -> int:
    values = job.copy()
    for field in (
        "location",
        "employment_type",
        "career_type",
        "ncs_categories",
        "required_qualifications",
        "preferred_qualifications",
    ):
        value = values[field]
        values[field] = json.dumps(value, ensure_ascii=False) if value is not None else None

    row = db.execute(
        """
        INSERT INTO job_posting (
            source, board_id, source_job_id, company, title, location,
            employment_type, career_type, ncs_categories, deadline, is_open,
            job_url, required_qualifications, preferred_qualifications, fetched_at
        ) VALUES (
            :source, :board_id, :source_job_id, :company, :title, :location,
            :employment_type, :career_type, :ncs_categories, :deadline, :is_open,
            :job_url, :required_qualifications, :preferred_qualifications, :fetched_at
        )
        ON CONFLICT (source, board_id, source_job_id) DO UPDATE SET
            company = excluded.company,
            title = excluded.title,
            location = excluded.location,
            employment_type = excluded.employment_type,
            career_type = excluded.career_type,
            ncs_categories = excluded.ncs_categories,
            deadline = excluded.deadline,
            is_open = excluded.is_open,
            job_url = excluded.job_url,
            required_qualifications = COALESCE(
                excluded.required_qualifications, job_posting.required_qualifications
            ),
            preferred_qualifications = COALESCE(
                excluded.preferred_qualifications, job_posting.preferred_qualifications
            ),
            fetched_at = excluded.fetched_at
        RETURNING id
        """,
        values,
    ).fetchone()
    return row[0]


def main() -> None:
    from dotenv import load_dotenv

    from backend.app.db.database import PROJECT_ROOT, connect_db

    load_dotenv(PROJECT_ROOT / ".env")

    jobs = fetch_jobs()
    print(f"첫 페이지 공고 {len(jobs)}건")
    if not jobs:
        return

    raw = jobs[0]
    job = normalize_job(raw)
    source_job_id = job["source_job_id"]
    detail = fetch_job_detail(source_job_id)
    job = normalize_job(raw, detail)

    with closing(connect_db()) as db:
        with db:
            job_id = upsert_job(db, job)

        saved = db.execute(
            "SELECT title, required_qualifications FROM job_posting WHERE id = ?",
            (job_id,),
        ).fetchone()

    print(f"저장 완료 | DB ID {job_id} | 잡알리오 {source_job_id}")
    print(saved["title"])
    print("필수 자격 필드 저장:", saved["required_qualifications"] is not None)


if __name__ == "__main__":
    try:
        main()
    except AlioError as error:
        raise SystemExit(f"{error.code} - {error}") from None