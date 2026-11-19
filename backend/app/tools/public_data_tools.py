import os
import re
from datetime import datetime
from itertools import count

import requests
from langchain_core.tools import tool

BASE_URL = "https://apis.data.go.kr/B490007"
NCS_PATH = "/hrdkapi/NCS005"
QNET_PATH = "/qualExamSchd/getQualExamSchdList"

QNET_DATE_FIELDS = {
    "written_registration_start": "docRegStartDt",
    "written_registration_end": "docRegEndDt",
    "written_exam_start": "docExamStartDt",
    "written_exam_end": "docExamEndDt",
    "written_result_date": "docPassDt",
    "practical_registration_start": "pracRegStartDt",
    "practical_registration_end": "pracRegEndDt",
    "practical_exam_start": "pracExamStartDt",
    "practical_exam_end": "pracExamEndDt",
    "practical_result_date": "pracPassDt",
}


class PublicDataError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _failure(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}


def _fetch_page(path: str, params: dict) -> tuple[list[dict], int]:
    api_key = os.getenv("DATA_GO_KR_KEY")
    if not api_key:
        raise PublicDataError("MISSING_API_KEY", "DATA_GO_KR_KEY를 설정해 주세요.")

    try:
        response = requests.get(
            f"{BASE_URL}{path}",
            params={**params, "serviceKey": api_key},
            timeout=(5, 15),
            allow_redirects=False,
        )
    except requests.Timeout:
        raise PublicDataError(
            "UPSTREAM_TIMEOUT", "공공데이터 API 응답 시간이 초과됐습니다."
        ) from None
    except requests.RequestException:
        raise PublicDataError(
            "UPSTREAM_ERROR", "공공데이터 API에 연결하지 못했습니다."
        ) from None

    if response.status_code != 200:
        raise PublicDataError(
            "UPSTREAM_ERROR", f"공공데이터 API HTTP 상태 {response.status_code}"
        )

    try:
        payload = response.json()
        result = payload.get("response", payload)
        if str(result["header"]["resultCode"]) not in {"00", "0"}:
            raise PublicDataError("UPSTREAM_ERROR", "공공데이터 API가 오류를 반환했습니다.")

        body = result["body"]
        total = int(body["totalCount"])
        items = body.get("items") or []

        if isinstance(items, dict):
            items = items.get("item", items) or []
        if isinstance(items, dict):
            items = [items]
    except (AttributeError, KeyError, TypeError, ValueError):
        raise PublicDataError(
            "UPSTREAM_INVALID_RESPONSE", "공공데이터 응답 형식이 올바르지 않습니다."
        ) from None

    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise PublicDataError(
            "UPSTREAM_INVALID_RESPONSE", "공공데이터 목록 형식이 올바르지 않습니다."
        )

    return items, total


@tool
def search_ncs_skills(ncs_code: str, limit: int = 20) -> dict:
    """확인된 NCS 8자리 직무 세분류 코드로 최신 차수의 능력단위를 조회한다."""
    ncs_code = ncs_code.strip()
    if not re.fullmatch(r"[0-9]{8}", ncs_code) or limit < 1:
        return _failure("INVALID_INPUT", "NCS 8자리 코드와 1 이상의 limit이 필요합니다.")

    params = {
        "NCS_LCLAS_CD": ncs_code[:2],
        "NCS_MCLAS_CD": ncs_code[2:4],
        "NCS_SCLAS_CD": ncs_code[4:6],
        "NCS_SUBD_CD": ncs_code[6:],
        "numOfRows": 50,
    }

    skills = {}
    try:
        for page in count(1):
            items, total = _fetch_page(NCS_PATH, {**params, "pageNo": page})

            for item in items:
                code = item.get("NCS_CL_CD")
                name = item.get("COMPE_UNIT_NAME")
                if item.get("USG_YN") == "Y" and name:
                    skills.setdefault(
                        code or name,
                        {
                            "code": code,
                            "name": name,
                            "definition": item.get("COMPE_UNIT_DEF"),
                            "level": item.get("COMPE_UNIT_LEVEL"),
                            "version": item.get("VER_NO"),
                        },
                    )

            if not items or page * params["numOfRows"] >= total or len(skills) >= limit:
                break
    except PublicDataError as error:
        return _failure(error.code, str(error))

    if not skills:
        return _failure("NO_RESULT", "해당 NCS 직무의 최신 능력단위가 없습니다.")

    results = list(skills.values())[:limit]
    return {
        "ok": True,
        "data": {"ncs_code": ncs_code, "count": len(results), "skills": results},
    }


def _date(value: str | int | None) -> str | None:
    digits = str(value or "").strip()
    if not re.fullmatch(r"[0-9]{8}", digits):
        return None

    try:
        return datetime.strptime(digits, "%Y%m%d").date().isoformat()
    except ValueError:
        return None


@tool
def get_cert_schedule(
    year: int,
    qualification_type: str = "T",
    qualification_code: str | None = None,
    page: int = 1,
    page_size: int = 10,
) -> dict:
    """연도와 자격구분으로 시험 일정을 조회한다. 특정 종목은 확인된 종목코드를 전달한다."""
    qualification_type = qualification_type.strip().upper()

    if not 1000 <= year <= 9999 or page < 1 or page_size < 1:
        return _failure(
            "INVALID_INPUT", "4자리 연도와 1 이상의 페이지·페이지 크기가 필요합니다."
        )
    if qualification_type not in {"T", "C", "W", "S"}:
        return _failure("INVALID_INPUT", "자격구분은 T, C, W, S 중 하나여야 합니다.")

    params = {
        "implYy": year,
        "qualgbCd": qualification_type,
        "dataFormat": "json",
        "pageNo": page,
        "numOfRows": page_size,
    }

    if qualification_code is not None:
        qualification_code = qualification_code.strip()
        if not re.fullmatch(r"[0-9]{4}", qualification_code):
            return _failure("INVALID_INPUT", "자격 종목코드는 숫자 4자리여야 합니다.")
        params["jmCd"] = qualification_code

    try:
        items, total = _fetch_page(QNET_PATH, params)
    except PublicDataError as error:
        return _failure(error.code, str(error))

    if not items:
        return _failure("NO_RESULT", "조회 조건에 해당하는 시험 일정이 없습니다.")

    schedules = [
        {
            "description": item.get("description"),
            "round": item.get("implSeq"),
            **{key: _date(item.get(field)) for key, field in QNET_DATE_FIELDS.items()},
        }
        for item in items
    ]
    return {
        "ok": True,
        "data": {"page": page, "total": total, "schedules": schedules},
    }


if __name__ == "__main__":
    from dotenv import load_dotenv

    from backend.app.db.database import PROJECT_ROOT

    load_dotenv(PROJECT_ROOT / ".env")

    ncs = search_ncs_skills.invoke({"ncs_code": "20010202", "limit": 5})
    if ncs["ok"]:
        print(f"search_ncs_skills 성공 | {ncs['data']['count']}건")
        print(ncs["data"]["skills"][0]["name"])
    else:
        print("search_ncs_skills 실패", ncs["error"])

    qnet = get_cert_schedule.invoke({"year": 2026, "page_size": 5})
    if qnet["ok"]:
        data = qnet["data"]
        print(
            f"get_cert_schedule 성공 | "
            f"이번 페이지 {len(data['schedules'])}건 / 전체 {data['total']}건"
        )
        print(data["schedules"][0]["description"])
    else:
        print("get_cert_schedule 실패", qnet["error"])

    if not ncs["ok"] or not qnet["ok"]:
        raise SystemExit(1)