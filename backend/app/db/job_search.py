import argparse
import json
import re
from contextlib import closing
from datetime import datetime, timedelta, timezone

from backend.app.db.database import connect_db
from backend.app.job_taxonomy import match_job, search_priority
from backend.app.job_sources import ATS_SOURCES

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
    source: str | None = None,
    allowed_ids: list[int] | None = None,
) -> list[dict]:
    if limit < 1:
        raise ValueError("limit은 1 이상이어야 합니다.")

    today = datetime.now(KST).date().isoformat()
    # A bounded RSS is a snapshot, not an authoritative list of all open jobs.
    rss_snapshot = ("AND id IN (SELECT CAST(value AS INTEGER) FROM json_each("
                    "(SELECT job_ids FROM job_source_snapshot WHERE source='incruit'))) "
                    "AND (SELECT item_count FROM job_source_snapshot WHERE source='incruit') > 0"
                    if source == "incruit" else "")

    with closing(connect_db()) as db:
        # Old ALIO-only databases remain readable before the first RSS migration.
        category_column = "rss_categories" if "rss_categories" in {
            row[1] for row in db.execute("PRAGMA table_info(job_posting)")
        } else "'[]' AS rss_categories"
        rows = db.execute(
            f"""
            SELECT id, source, board_id, source_job_id, company, title,
                   location, employment_type, career_type, ncs_categories,
                   deadline, is_open, job_url, {category_column},
                   CASE WHEN source='incruit' THEN description_html END AS rss_summary
            FROM job_posting
            WHERE (? IS NULL OR source = ?)
              AND (? = 0 OR ((is_open = 1 OR (source='incruit' AND is_open IS NULL))
                             AND (deadline IS NULL OR deadline >= ?)))
              {rss_snapshot}
            ORDER BY deadline IS NULL, deadline, id
            """,
            (source, source, ongoing_only, today),
        ).fetchall()

    region = region.strip().casefold()
    employment_type = employment_type.strip().casefold()
    ncs_category = ncs_category.strip().casefold()
    career_type = career_type.strip().casefold()

    results = []
    allowed = set(allowed_ids) if allowed_ids is not None else None
    for row in rows:
        job = dict(row)
        if allowed is not None and job['id'] not in allowed:
            continue

        for field in ("location", "employment_type", "career_type", "ncs_categories", "rss_categories"):
            if job[field] is not None:
                job[field] = json.loads(job[field])

        matching = match_job(job, keyword)
        if matching is None:
            continue
        if region:
            from backend.app.job_dedup import region_matches
            if not region_matches(region,job['location']):continue
        job['unverified_filters'] = []
        if job['source'] in ATS_SOURCES:
            if employment_type and employment_type not in {s.casefold() for s in job['employment_type'] or []}:
                explicit = set(job['employment_type'] or []) & {'정규직','계약직','인턴','비정규직'}
                if explicit:continue
                job['unverified_filters'].append('employment_type')
            if ncs_category:job['unverified_filters'].append('ncs_category')
            if career_type and not job['career_type']:job['unverified_filters'].append('career_type')
        if job['source'] not in {'incruit',*ATS_SOURCES} and employment_type and employment_type not in {
            item.casefold() for item in (job["employment_type"] or [])
        }:
            continue
        if job['source'] not in {'incruit',*ATS_SOURCES} and ncs_category and not any(
            ncs_category in item.casefold() for item in (job["ncs_categories"] or [])
        ):
            continue

        career_types = {
            part.strip().casefold()
            for item in (job["career_type"] or [])
            for part in re.split(r"[+,/·]", item)
        }
        if career_type and career_type not in career_types and not (job['source'] in ATS_SOURCES and not career_types):
            continue

        job["search_match"] = matching
        results.append(job)

    # Rank all matches before applying limit, so later direct matches cannot be lost.
    results.sort(key=search_priority)
    return results[:limit]


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
