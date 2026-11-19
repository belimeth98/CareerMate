
import requests

BASE_URL = "https://boards-api.greenhouse.io/v1/boards"

# 테스트할 기업의 채용 게시판 식별자
COMPANIES = {
    "크래프톤": "krafton",
    "GitLab": "gitlab",
}

# 개발 직무 관련 검색어
KEYWORDS = [
    "backend", "back-end", "software",
    "engineer", "developer",
    "백엔드", "서버", "개발"
]


def test_greenhouse(company, board_id):
    print(f"\n===== {company} 채용공고 조회 =====")

    url = f"{BASE_URL}/{board_id}/jobs"

    try:
        response = requests.get(
            url,
            params={"content": "true"},
            timeout=(5, 20),
        )

        print("HTTP 상태코드:", response.status_code)
        response.raise_for_status()

        data = response.json()
        jobs = data.get("jobs", [])

        print("전체 공고 수:", len(jobs))

        # 제목에 개발 관련 키워드가 있는 공고 검색
        matched = [
            job for job in jobs
            if any(
                keyword in job.get("title", "").lower()
                for keyword in KEYWORDS
            )
        ]

        print("개발 관련 공고 수:", len(matched))

        # 최대 5개만 출력
        for job in matched[:5]:
            print("---------------------")
            print("공고 ID:", job.get("id"))
            print("제목:", job.get("title"))
            print(
                "근무지역:",
                job.get("location", {}).get("name")
            )
            print("공고 URL:", job.get("absolute_url"))
            print("수정일:", job.get("updated_at"))

        # 상세정보 조회
        if matched:
            job_id = matched[0]["id"]
            detail_url = f"{url}/{job_id}"

            detail_response = requests.get(
                detail_url,
                timeout=(5, 20),
            )
            detail_response.raise_for_status()

            detail = detail_response.json()

            print("\n===== 상세 조회 =====")
            print("제목:", detail.get("title"))
            print(
                "상세 설명 존재:",
                bool(detail.get("content"))
            )
            print(
                "마감일:",
                detail.get("application_deadline")
            )

    except requests.RequestException as e:
        print("API 호출 오류:", type(e).__name__)

    except ValueError:
        print("JSON 응답 파싱 실패")


for company, board_id in COMPANIES.items():
    test_greenhouse(company, board_id)
