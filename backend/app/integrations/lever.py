import requests
from html import escape

BASE_URL = "https://api.lever.co/v0/postings"


def fetch_jobs(board_id: str) -> list[dict]:
    response = requests.get(
        f"{BASE_URL}/{board_id}",
        params={"mode": "json", "limit": 100, "skip": 0},
        timeout=(5, 20),
    )
    response.raise_for_status()
    return response.json()


def fetch_job_detail(board_id: str, job_id: str) -> dict:
    response = requests.get(
        f"{BASE_URL}/{board_id}/{job_id}",
        timeout=(5, 20),
    )
    response.raise_for_status()
    return response.json()

def normalize_job(board_id: str, job: dict, company_name: str) -> dict:
    categories = job.get("categories") or {}
    parts = [job.get("description")]

    for section in job.get("lists") or []:
        heading = section.get("text")
        content = section.get("content")

        if heading:
            parts.append(f"<h3>{escape(heading)}</h3>")
        if content:
            parts.append(f"<ul>{content}</ul>")

    parts.append(job.get("additional"))

    return {
        "source": "lever",
        "board_id": board_id,
        "source_job_id": str(job["id"]),
        "company": company_name,
        "title": job["text"],
        "location": categories.get("location"),
        "employment_type": categories.get("commitment"),
        "deadline": None,
        "job_url": job.get("hostedUrl"),
        "description_html": "\n".join(part for part in parts if part) or None,
    }