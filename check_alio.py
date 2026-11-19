from dotenv import load_dotenv

from backend.app.integrations.alio import AlioError, fetch_jobs, normalize_job

load_dotenv()

try:
    jobs = fetch_jobs()
    print(f"첫 페이지 공고 {len(jobs)}건")
    if jobs:
        print(normalize_job(jobs[0]))
except AlioError as error:
    raise SystemExit(f"{error.code} - {error}") from None