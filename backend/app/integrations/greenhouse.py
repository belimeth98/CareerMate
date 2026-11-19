import requests
from html import unescape

BASE_URL = "https://boards-api.greenhouse.io/v1/boards"


def fetch_jobs(board_id: str) -> list[dict]:
    url = f"{BASE_URL}/{board_id}/jobs"
    response = requests.get(url, timeout=(5, 20))
    response.raise_for_status()

    return response.json()["jobs"]

def fetch_job_detail(board_id: str, job_id: int) -> dict:
    url = f"{BASE_URL}/{board_id}/jobs/{job_id}"
    response = requests.get(url, timeout=(5, 20))
    response.raise_for_status()

    return response.json()

def normalize_job(board_id: str, job: dict) -> dict:
    employment_type = None
    for item in job.get("metadata") or []:
        if item.get("name") == "Employment Type":
            employment_type = item.get("value")
            break

    content = job.get("content")

    return {
        "source": "greenhouse",
        "board_id": board_id,
        "source_job_id": str(job["id"]),
        "company": job.get("company_name"),
        "title": job["title"],
        "location": (job.get("location") or {}).get("name"),
        "employment_type": employment_type,
        "deadline": job.get("application_deadline"),
        "job_url": job.get("absolute_url"),
        "description_html": unescape(content) if content else None,
    }