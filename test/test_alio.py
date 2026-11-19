
import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("ALIO_API_KEY")
BASE_URL = "https://opendata.alio.go.kr"

if not API_KEY:
    raise ValueError("ALIO_API_KEY가 없습니다.")


def call_alio_api(endpoint, extra_params=None):
    """잡알리오 OpenAPI 공통 호출 함수"""

    url = BASE_URL + endpoint

    params = {
        "serviceKey": API_KEY,
        "resultType": "json",
    }

    if extra_params:
        params.update(extra_params)

    try:
        response = requests.post(
            url,
            params=params,
            timeout=(5, 20),
            allow_redirects=False,
        )

        print("HTTP 상태코드:", response.status_code)

        if response.status_code != 200:
            print("API HTTP 요청 실패")
            # 인증키가 출력되지 않도록 마스킹
            print(response.text[:300].replace(API_KEY, "***"))
            return None

        data = response.json()

        print("API 결과코드:", data.get("resultCode"))
        print("API 결과메시지:", data.get("resultMsg"))

        # 명세에서 0(정상), 200(성공)으로 표시
        if str(data.get("resultCode")) not in ("0", "200"):
            print("API 응답 오류")
            return None

        return data

    except requests.RequestException as e:
        print("네트워크 오류:", type(e).__name__)

    except ValueError:
        print("JSON 파싱 실패")

    return None



# ==========================================
# 1. 전체 채용공고 조회
# ==========================================

all_jobs = []
page = 1
page_size = 50

print("\n===== 잡알리오 전체 채용공고 조회 =====")

while True:
    data = call_alio_api(
        "/new/v1/recruit/list.do",
        {
            "pageNo": page,
            "numOfRows": page_size,
            "ongoingYn": "Y",
        },
    )

    if data is None:
        print("조회 중 오류 발생")
        break

    jobs = data.get("result") or []

    if isinstance(jobs, dict):
        jobs = [jobs]

    if not isinstance(jobs, list):
        print("예상하지 못한 데이터 구조")
        break

    all_jobs.extend(jobs)

    total_count = int(data.get("totalCount") or 0)

    print(f"{page}페이지 조회: {len(jobs)}건")

    if not jobs or len(all_jobs) >= total_count:
        break

    page += 1


# ==========================================
# 2. 중복 제거
# ==========================================

unique_jobs = {
    job["recrutPblntSn"]: job
    for job in all_jobs
    if job.get("recrutPblntSn") is not None
}

all_jobs = list(unique_jobs.values())

print("\n수집한 전체 공고:", len(all_jobs))



import re

# ==========================================
# 3. IT 직무 필터링
# ==========================================

# "개발"은 너무 광범위해서 제외
IT_KEYWORDS = [
    "전산",
    "정보기술",
    "정보보안",
    "정보시스템",
    "소프트웨어",
    "데이터",
    "서버",
    "백엔드",
    "프론트엔드",
    "클라우드",
    "인프라",
    "네트워크",
    "시스템 운영",
    "웹개발",
    "개발자",
    "인공지능",
]

filtered_jobs = []

for job in all_jobs:
    title = str(job.get("recrutPbancTtl") or "")
    ncs = str(job.get("ncsCdNmLst") or "")

    # 공고 제목에서 IT 키워드 검색
    title_match = any(
        keyword in title
        for keyword in IT_KEYWORDS
    )

    # 영문 기술 키워드는 독립된 단어로 검색
    english_match = bool(
        re.search(
            r"(?<![A-Za-z0-9])(AI|IT|ICT|SW)"
            r"(?![A-Za-z0-9])",
            title,
            re.IGNORECASE,
        )
    )

    # NCS의 정보통신 분류 포함 여부
    ncs_match = "정보통신" in ncs

    if title_match or english_match or ncs_match:
        filtered_jobs.append({
            **job,
            "match_reason": (
                "제목 키워드"
                if title_match or english_match
                else "NCS 직무분류"
            )
        })


# ==========================================
# 4. 검색 결과 출력
# ==========================================

print("\n===== IT 관련 공고 후보 =====")
print("후보 공고 수:", len(filtered_jobs))

for job in filtered_jobs:
    print("--------------------------")
    print("기관명:", job.get("instNm"))
    print("공고명:", job.get("recrutPbancTtl"))
    print("채용구분:", job.get("recrutSeNm"))
    print("고용형태:", job.get("hireTypeNmLst"))
    print("NCS 분류:", job.get("ncsCdNmLst"))
    print("검색 근거:", job.get("match_reason"))
    print("공고번호:", job.get("recrutPblntSn"))
