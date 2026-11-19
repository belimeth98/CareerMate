
import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("DATA_GO_KR_KEY")

# 한국산업인력공단_국가자격 시험일정 조회 서비스 테스트
URL = "https://apis.data.go.kr/B490007/qualExamSchd/getQualExamSchdList"

if not API_KEY:
    raise ValueError("DATA_GO_KR_KEY가 설정되지 않았습니다.")

params = {
    "serviceKey": API_KEY,
    "numOfRows": 5,
    "pageNo": 1,
    "dataFormat": "json",
    "implYy": "2026",
    "qualgbCd": "T",  # 국가기술자격
}

try:
    response = requests.get(
        URL,
        params=params,
        timeout=(5, 15),
        allow_redirects=False
    )

    print("HTTP 상태코드:", response.status_code)

    if response.status_code != 200:
            print("API 요청 실패")
            # 요청 URL과 인증키는 출력하지 않음
            print("응답 내용은 로컬에서 확인해주세요.")
    else:
        data = response.json()
        print("API 응답:", data)
        result = data.get("response", {})
        header = result.get("header", {})
        body = result.get("body", {})

        print("결과코드:", header.get("resultCode"))
        print("결과메시지:", header.get("resultMsg"))
        print("전체 데이터 수:", body.get("totalCount"))

        if str(header.get("resultCode")) == "00":
            items = body.get("items", {})
            if isinstance(items, dict):
                items = items.get("item", [])
            if isinstance(items, dict):
                items = [items]

            for item in items or []:
                print("------------------")
                print("회차:", item.get("description"))
                print("필기 접수:", item.get("docRegStartDt"))
                print("필기 시험:", item.get("docExamStartDt"))

except requests.RequestException as e:
    print("네트워크 오류:", type(e).__name__)

except ValueError:
    print("JSON 형식의 응답을 받지 못했습니다.")
