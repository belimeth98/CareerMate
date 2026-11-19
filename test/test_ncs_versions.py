
import os
import math
import requests
from collections import Counter
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("DATA_GO_KR_KEY")
URL = "https://apis.data.go.kr/B490007/hrdkapi/NCS005"

if not API_KEY:
    raise ValueError("DATA_GO_KR_KEY가 없습니다.")

params = {
    "serviceKey": API_KEY,
    "NCS_LCLAS_CD": "20",
    "NCS_MCLAS_CD": "01",
    "NCS_SCLAS_CD": "02",
    "NCS_SUBD_CD": "02",
    "numOfRows": 50,
}

all_items = []

try:
    # 첫 페이지 조회
    for page in range(1, 10):
        params["pageNo"] = page

        response = requests.get(
            URL,
            params=params,
            timeout=(5, 15),
            allow_redirects=False
        )
        response.raise_for_status()

        data = response.json()
        result = data.get("response", data)

        header = result.get("header", {})
        if str(header.get("resultCode")) != "00":
            print("API 응답 오류:", header.get("resultMsg"))
            break

        body = result.get("body", {})
        items = body.get("items", [])

        if isinstance(items, dict):
            items = items.get("item", [])
        if isinstance(items, dict):
            items = [items]

        all_items.extend(items)
        total = int(body.get("totalCount", 0))

        print(f"{page}페이지: {len(items)}건 조회")

        if page >= math.ceil(total / 50):
            break

    print("\n===== 전체 조회 결과 =====")
    print("수집 건수:", len(all_items))

    print("최신 차수 여부:", Counter(
        item.get("USG_YN") for item in all_items
    ))

    print("NCS 차수별 건수:", Counter(
        item.get("NCS_DEGR") for item in all_items
    ))

    latest_items = [
        item for item in all_items
        if item.get("USG_YN") == "Y"
    ]

    print("\n===== Y로 표시된 능력단위 =====")
    for item in latest_items[:15]:
        print(
            item.get("COMPE_UNIT_NAME"),
            "| 개발연도:", item.get("DEVEL_YY"),
            "| 버전:", item.get("VER_NO")
        )

except requests.RequestException as e:
    print("요청 오류:", type(e).__name__)

except ValueError as e:
    print("응답 데이터 처리 오류:", type(e).__name__)
