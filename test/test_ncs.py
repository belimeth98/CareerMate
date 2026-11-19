
import os
import requests
from dotenv import load_dotenv
from collections import Counter

load_dotenv()

API_KEY = os.getenv("DATA_GO_KR_KEY")
BASE_URL = "https://apis.data.go.kr/B490007/hrdkapi"

if not API_KEY:
    raise ValueError("DATA_GO_KR_KEY가 없습니다.")


def test_api(name, endpoint, params):
    print(f"\n===== {name} =====")

    url = f"{BASE_URL}/{endpoint}"

    params = {
        "serviceKey": API_KEY,
        "pageNo": 1,
        "numOfRows": 5,
        **params,
    }

    try:
        response = requests.get(
            url,
            params=params,
            timeout=(5, 15),
            allow_redirects=False,
        )

        print("HTTP 상태코드:", response.status_code)

        if response.status_code != 200:
            print("요청 실패: 상태코드와 파라미터 확인 필요")
            return

        data = response.json()

        # API에 따라 response로 감싸진 경우 처리
        result = data.get("response", data)
        header = result.get("header", {})
        body = result.get("body", {})

        print("결과코드:", header.get("resultCode"))
        print("결과메시지:", header.get("resultMsg"))

        if str(header.get("resultCode")) != "00":
            print("API 응답 확인 필요")
            print("최상위 필드:", list(result.keys()))
            return

        print("데이터 조회 성공!")
        print("전체 건수:", body.get("totalCount"))

        items = body.get("items", [])
        if isinstance(items, dict):
            items = items.get("item", items)

        if isinstance(items, dict):
            items = [items]

        # ==========
        if isinstance(items, list):
            print("현재 페이지 데이터 수:", len(items))

            # 최신 차수 여부 확인
            print("최신 차수 여부:", Counter(
                item.get("USG_YN") for item in items
            ))

            # 능력단위 정보 출력
            for item in items[:10]:
                print(
                    item.get("COMPE_UNIT_NAME"),
                    "| 차수:", item.get("NCS_DEGR"),
                    "| 버전:", item.get("VER_NO"),
                    "| 최신:", item.get("USG_YN")
                )
        else:
            print("응답 데이터 구조:", type(items).__name__)
        # ==========

    except requests.RequestException as e:
        print("네트워크 오류:", type(e).__name__)

    except ValueError:
        print("JSON 파싱 실패")


# 1. NCS 대분류 조회
# test_api(
#     name="NCS 대분류 조회",
#     endpoint="NCS001",
#     params={},
# )

# 2. NCS 능력단위 키워드 검색
# test_api(
#     name="소프트웨어 직무 역량 검색",
#     endpoint="NCS007",
#     params={
#         "LVL": 1,
#         "SWRD": "소프트웨어",
#         "SNUM": 1,
#         "ENUM": 5,
#     },
# )

# 응용SW엔지니어링 능력단위 조회
test_api(
    name="응용SW엔지니어링 능력단위 조회",
    endpoint="NCS005",
    params={
        "NCS_LCLAS_CD": "20",  # 정보통신
        "NCS_MCLAS_CD": "01",  # 정보기술
        "NCS_SCLAS_CD": "02",  # 정보기술개발
        "NCS_SUBD_CD": "02",   # 응용SW엔지니어링
        "numOfRows": 50,
    },
)
