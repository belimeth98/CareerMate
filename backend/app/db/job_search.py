import argparse
import json
import re
from contextlib import closing
from datetime import datetime, timedelta, timezone

from backend.app.db.database import connect_db

KST = timezone(timedelta(hours=9))


def search_jobs(
    keyword: str = "",
    region: str = "",
    employment_type: str = "",
    ncs_category: str = "",
    career_type: str = "",
    *,
    ongoing_only: bool = True,
    limit: int = 20,
) -> list[dict]:
    if limit < 1:
        raise ValueError("limit은 1 이상이어야 합니다.")

    today = datetime.now(KST).date().isoformat()

    with closing(connect_db()) as db:
        rows = db.execute(
            """
            SELECT id, source, board_id, source_job_id, company, title,
                   location, employment_type, career_type, ncs_categories,
                   deadline, is_open, job_url
            FROM job_posting
            WHERE ? = 0 OR (is_open = 1 AND (deadline IS NULL OR deadline >= ?))
            ORDER BY deadline IS NULL, deadline, id
            """,
            (ongoing_only, today),
        ).fetchall()

    terms = keyword.casefold().split()
    region = region.strip().casefold()
    employment_type = employment_type.strip().casefold()
    ncs_category = ncs_category.strip().casefold()
    career_type = career_type.strip().casefold()

    results = []
    for row in rows:
        job = dict(row)

        for field in ("location", "employment_type", "career_type", "ncs_categories"):
            if job[field] is not None:
                job[field] = json.loads(job[field])

        text = f"{job['title']} {job['company']}".casefold()
        if not all(term in text for term in terms):
            continue
        if region and not any(
            region in item.casefold() for item in (job["location"] or [])
        ):
            continue
        if employment_type and employment_type not in {
            item.casefold() for item in (job["employment_type"] or [])
        }:
            continue
        if ncs_category and not any(
            ncs_category in item.casefold() for item in (job["ncs_categories"] or [])
        ):
            continue

        career_types = {
            part.strip().casefold()
            for item in (job["career_type"] or [])
            for part in re.split(r"[+,/·]", item)
        }
        if career_type and career_type not in career_types:
            continue

        results.append(job)
        if len(results) == limit:
            break

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="SQLite에 저장된 채용공고 검색")
    parser.add_argument("keyword", nargs="?", default="")
    parser.add_argument("--region", default="")
    parser.add_argument("--employment-type", default="")
    parser.add_argument("--ncs-category", default="")
    parser.add_argument("--career-type", default="")
    parser.add_argument("--all-statuses", action="store_true")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    try:
        jobs = search_jobs(
            keyword=args.keyword,
            region=args.region,
            employment_type=args.employment_type,
            ncs_category=args.ncs_category,
            career_type=args.career_type,
            ongoing_only=not args.all_statuses,
            limit=args.limit,
        )
    except ValueError as error:
        parser.error(str(error))

    print(f"검색 결과 {len(jobs)}건 표시")
    for job in jobs:
        print(f"[DB ID {job['id']}] {job['company']} | {job['title']}")


if __name__ == "__main__":
    main()