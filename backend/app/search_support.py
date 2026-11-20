"""Unified retrieval keeps provider failures and incomplete coverage explicit."""

from concurrent.futures import ThreadPoolExecutor
from itertools import zip_longest
from backend.app.job_taxonomy import search_priority


def unified_search(invoke, arguments, *, sources=("alio", "incruit"), postprocess=None):
    def run(source):
        try:
            return source, invoke({**arguments, "source": source})
        except Exception:
            return source, {"ok": False, "error": {"code": "TOOL_ERROR", "message": "출처 조회 실패"}}
    with ThreadPoolExecutor(max_workers=len(sources)) as pool:
        results = list(pool.map(run, sources))
    raw_results=dict(results)
    if postprocess:
        results=[(source,postprocess(source,result,raw_results)) for source,result in results]
    statuses, lists = [], []
    for source, result in results:
        if result["ok"]:
            data = result["data"]
            lists.append(data["jobs"])
            statuses.append({"source": source, "status": "error" if data.get('partial_failure') else "ok", "count": len(data["jobs"]),
                             "data_source": data.get("data_source", "cache"),
                             **({'error_code':'UPSTREAM_PARTIAL_FAILURE'} if data.get('partial_failure') else {}),
                             **({'duplicate_count':data['duplicate_count']} if 'duplicate_count' in data else {})})
        else:
            code = result["error"]["code"]
            lists.append([])
            statuses.append({"source": source, "status": "no_result" if code == "NO_RESULT" else "error",
                             "error_code": code,**({'duplicate_count':result['duplicate_count']} if 'duplicate_count' in result else {})})
    jobs, seen = [], set()
    for pair in zip_longest(*lists):
        for job in pair:
            if job is not None and job["id"] not in seen:
                jobs.append(job); seen.add(job["id"])
    jobs.sort(key=search_priority)
    jobs = jobs[:arguments["limit"]]
    failures = any(row["status"] == "error" for row in statuses)
    if not jobs:
        return {"ok": False, "error": {
            "code": "UPSTREAM_PARTIAL_FAILURE" if failures else "NO_RESULT",
            "message": "일부 출처를 조회하지 못해 통합 검색 결과를 확정하지 못했습니다." if failures else "수집 범위에서 조건에 맞는 공고가 없습니다.",
        }, "source_results": statuses,"duplicate_count":sum(row.get('duplicate_count',0) for row in statuses)}
    return {"ok": True, "data": {"jobs": jobs, "count": len(jobs), "source": "all",
            "data_source": "mixed", "source_results": statuses, "partial_failure": failures,
            "duplicate_count":sum(row.get('duplicate_count',0) for row in statuses),
            "notice": "설정된 출처의 공고를 통합 조회했습니다. 수집 범위와 미확인 조건은 출처별로 다르며 원문 확인이 필요합니다."}}


def suggested_terms(keyword):
    normalized = (keyword or "").casefold()
    groups = (
        (("백엔드", "backend", "프론트", "frontend", "서버", "python", "java", "개발"), ("IT", "소프트웨어", "정보통신", "전산")),
        (("약사", "간호", "의료"), ("보건", "의료", "병원")),
        (("데이터", "분석", "ai", "인공지능"), ("IT", "정보통신", "연구")),
    )
    for keys, terms in groups:
        if any(key in normalized for key in keys):
            return [term for term in terms if term.casefold() != normalized]
    return [term for term in ("IT", "사무", "연구", "보건") if term.casefold() != normalized]
