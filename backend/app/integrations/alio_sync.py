from contextlib import closing
from itertools import count

from dotenv import load_dotenv

from backend.app.db.database import PROJECT_ROOT, connect_db
from backend.app.integrations.alio import AlioError, fetch_jobs, normalize_job, upsert_job


def collect_open_jobs(page_size: int = 50) -> list[dict]:
    jobs_by_id = {}

    for page in count(1):
        raw_jobs = fetch_jobs(page=page, page_size=page_size)
        if not raw_jobs:
            break

        previous_count = len(jobs_by_id)
        batch = [normalize_job(raw) for raw in raw_jobs]

        for job in batch:
            jobs_by_id[job["source_job_id"]] = job

        print(f"{page}페이지 {len(batch)}건 | 누적 {len(jobs_by_id)}건")

        if len(batch) < page_size:
            break
        if len(jobs_by_id) == previous_count:
            raise AlioError(
                "UPSTREAM_INVALID_RESPONSE",
                "잡알리오가 같은 공고를 반복 반환했습니다.",
            )

    return list(jobs_by_id.values())


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    jobs = collect_open_jobs()

    with closing(connect_db()) as db:
        with db:
            for job in jobs:
                upsert_job(db, job)

    print(f"잡알리오 공고 {len(jobs)}건 저장·갱신 완료")


if __name__ == "__main__":
    try:
        main()
    except AlioError as error:
        raise SystemExit(f"{error.code} - {error}") from None