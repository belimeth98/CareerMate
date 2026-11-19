
import requests

BASE_URL = "https://api.lever.co/v0/postings"
COMPANY = "spotify"

KEYWORDS = [
    "backend", "back-end", "software",
    "engineer", "developer", "platform"
]


def test_lever():
    print("===== Spotify 채용공고 조회 =====")

    url = f"{BASE_URL}/{COMPANY}"

    try:
        response = requests.get(
            url,
            params={
                "mode": "json",
                "limit": 100,
                "skip": 0,
            },
            timeout=(5, 20),
        )

        print("HTTP 상태코드:", response.status_code)
        response.raise_for_status()

        jobs = response.json()

        print("이번 페이지 공고 수:", len(jobs))

        matched = [
            job for job in jobs
            if any(
                keyword in job.get("text", "").lower()
                for keyword in KEYWORDS
            )
        ]

        print("개발 관련 공고 수:", len(matched))

        for job in matched[:5]:
            print("---------------------")
            print("공고 ID:", job.get("id"))
            print("제목:", job.get("text"))
            print("국가:", job.get("country"))
            print(
                "근무지역:",
                job.get("categories", {}).get("location")
            )
            print(
                "고용형태:",
                job.get("categories", {}).get("commitment")
            )
            print("공고 URL:", job.get("hostedUrl"))

        # 공고 상세 조회
        if matched:
            job_id = matched[0]["id"]

            detail_response = requests.get(
                f"{url}/{job_id}",
                timeout=(5, 20),
            )
            detail_response.raise_for_status()

            detail = detail_response.json()

            print("\n===== 상세 조회 =====")
            print("제목:", detail.get("text"))
            print(
                "상세 설명 존재:",
                bool(detail.get("descriptionPlain"))
            )
            print("근무 방식:", detail.get("workplaceType"))
            print("지원 URL:", detail.get("applyUrl"))

    except requests.RequestException as e:
        print("API 호출 오류:", type(e).__name__)

    except (ValueError, TypeError) as e:
        print("응답 처리 오류:", type(e).__name__)


test_lever()
