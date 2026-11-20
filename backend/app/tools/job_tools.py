import json
import sqlite3
from contextlib import closing
from typing import Literal
from datetime import datetime, timezone

from langchain_core.tools import tool

from backend.app.db.database import connect_db
from backend.app.db.job_search import search_jobs as search_cached_jobs
from backend.app.integrations import alio
from backend.app.integrations.alio_sync import collect_open_jobs
from backend.app.integrations import incruit_rss
from backend.app.search_support import unified_search
from backend.app import ats_sources, job_dedup
from backend.app.job_sources import JOB_SOURCES, ATS_SOURCES
from backend.app.integrations import greenhouse, lever
from backend.app.integrations.ats_common import AtsError


def _failure(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}


def _read_ats(source,arguments):
    try:
        ids,boards=ats_sources.collect(source,arguments['refresh'])
        query={k:v for k,v in arguments.items() if k not in {'refresh','limit','source'}}
        jobs=search_cached_jobs(**query,source=source,limit=max(1,len(ids)),allowed_ids=ids)
        errors=[row for row in boards if row['status']=='error']
        if not jobs:
            if errors:return _failure('UPSTREAM_PARTIAL_FAILURE','설정된 국내 기업의 공고 또는 사용 가능한 캐시를 확인하지 못했습니다.')
            return _failure('NO_RESULT','설정된 국내 기업에서 조건에 맞는 공고가 없습니다.')
        return {'ok':True,'data':{'jobs':jobs,'count':len(jobs),'source':source,'board_results':boards,
               'partial_failure':bool(errors),'coverage':'configured_domestic_companies',
               'data_source':'external_api' if any(r.get('data_source')=='external_api' for r in boards) else 'cache'}}
    except AtsError as error:return _failure(error.code,str(error))
    except sqlite3.Error:return _failure('DATABASE_ERROR','국내 기업 채용 공고를 저장하거나 읽지 못했습니다.')
    except (AttributeError,KeyError,TypeError,ValueError):return _failure('UPSTREAM_INVALID_RESPONSE','국내 기업 채용 공고 형식을 확인하지 못했습니다.')


def _finish_ats(result,reference,limit):
    if not result['ok']:return result
    if not reference['ok'] and reference.get('error',{}).get('code')!='NO_RESULT':
        return _failure('DEDUP_REFERENCE_UNAVAILABLE','인크루트 조회 실패로 중복을 확인할 수 없어 추가 출처 노출을 보류했습니다.')
    try:
        jobs,removed=job_dedup.remove_incruit_duplicates(result['data']['jobs'])
    except AtsError as error:return _failure(error.code,str(error))
    except (sqlite3.Error,ValueError,TypeError,KeyError):return _failure('DATABASE_ERROR','인크루트 중복 확인 자료를 읽지 못했습니다.')
    if not jobs:
        return {**_failure('NO_RESULT','인크루트와 중복되는 공고를 제외한 결과가 없습니다.'),
                'duplicate_count':removed,'dedup_status':'checked'}
    jobs=jobs[:limit]
    return {'ok':True,'data':{**result['data'],'jobs':jobs,'count':len(jobs),'duplicate_count':removed,
            'dedup_status':'checked','dedup_scope':'latest_collected_incruit_rss'}}


@tool
def search_jobs(
    keyword: str = "",
    region: str = "",
    employment_type: str = "",
    ncs_category: str = "",
    career_type: str = "",
    ongoing_only: bool = True,
    limit: int = 20,
    refresh: bool = True,
    source: Literal["all", "alio", "incruit", "greenhouse", "lever"] = "alio",
) -> dict:
    """통합 또는 지정한 출처의 공고를 갱신·검색하고 상세 조회용 DB id를 반환한다.

    refresh의 기본값은 True다. 같은 요청의 재계획에서는 False로 이미 확보한
    후보를 재사용한다. 인크루트는 일부 RSS 후보이며 진행·고용형태·NCS는 미확인이다.
    Greenhouse·Lever는 설정된 국내 기업의 공개 공고만 조회하고 수집한 인크루트와 중복은 제외한다.
    외부 수집 실패는 오류로 반환하며 정상 0건과 구분한다.
    """
    if limit < 1:
        return _failure("INVALID_INPUT", "limit은 1 이상이어야 합니다.")
    arguments=dict(keyword=keyword,region=region,employment_type=employment_type,ncs_category=ncs_category,
                   career_type=career_type,ongoing_only=ongoing_only,limit=limit,refresh=refresh)
    if source == "all":
        def invoke(args):
            if args['source'] in ATS_SOURCES:return _read_ats(args['source'],args)
            return search_jobs.invoke(args)
        def postprocess(provider,result,results):
            return _finish_ats(result,results['incruit'],limit) if provider in ATS_SOURCES else result
        return unified_search(invoke,arguments,sources=JOB_SOURCES,postprocess=postprocess)
    if source in ATS_SOURCES:
        reference=search_jobs.invoke({'source':'incruit','refresh':refresh,'limit':1})
        return _finish_ats(_read_ats(source,arguments),reference,limit)

    try:
        refreshed = refresh
        if source == "incruit":
            with closing(connect_db()) as db:
                incruit_rss.ensure_snapshot_table(db)
                refreshed = refresh and not incruit_rss.snapshot_is_fresh(db)
            if refreshed:
                rss_jobs = incruit_rss.collect_jobs()
                with closing(connect_db()) as db, db:
                    stamp = rss_jobs[0]["fetched_at"] if rss_jobs else datetime.now(timezone.utc).isoformat(timespec="microseconds")
                    snapshot_ids = []
                    for posting in rss_jobs:
                        job_id = alio.upsert_job(db, posting)
                        snapshot_ids.append(job_id)
                        db.execute("UPDATE job_posting SET description_html=?,rss_categories=? WHERE id=?",
                                   (posting["description_html"], json.dumps(posting.get("rss_categories", [])), job_id))
                    db.execute("""INSERT INTO job_source_snapshot(source,fetched_at,item_count,job_ids) VALUES('incruit',?,?,?)
                                  ON CONFLICT(source) DO UPDATE SET fetched_at=excluded.fetched_at,item_count=excluded.item_count,job_ids=excluded.job_ids""",
                               (stamp, len(rss_jobs), json.dumps(snapshot_ids)))
        elif refresh:
            # Collect completely before writing: failed/partial snapshots cannot
            # close existing jobs or masquerade as a successful empty search.
            jobs_from_api = collect_open_jobs(quiet=True)
            with closing(connect_db()) as db, db:
                db.execute("UPDATE job_posting SET is_open = 0 WHERE source = 'alio' AND board_id = 'alio'")
                for job in jobs_from_api:
                    alio.upsert_job(db, job)

        jobs = search_cached_jobs(
            keyword=keyword,
            region=region,
            employment_type=employment_type,
            ncs_category=ncs_category,
            career_type=career_type,
            ongoing_only=ongoing_only,
            limit=limit,
            source=source,
        )
    except (alio.AlioError, incruit_rss.RssError) as error:
        return _failure(error.code, str(error))
    except (sqlite3.Error, json.JSONDecodeError):
        return _failure("DATABASE_ERROR", "저장된 공고를 읽지 못했습니다.")
    except (AttributeError, KeyError, TypeError, ValueError):
        return _failure("UPSTREAM_INVALID_RESPONSE", "공고 목록 응답 형식을 확인하지 못했습니다.")

    if not jobs:
        return _failure("NO_RESULT", "검색 조건에 맞는 공고가 없습니다.")

    data = {
        "jobs": jobs, "count": len(jobs),
        "source": source,
        "data_source": ("rss" if source == "incruit" else "external_api") if refreshed else "cache",
    }
    if source == "incruit":
        data.update(notice=incruit_rss.NOTICE, coverage="rss_subset",
                    unverified_filters=[key for key, value in (
                        ("employment_type", employment_type), ("ncs_category", ncs_category)) if value])
    return {"ok": True, "data": data}


@tool
def get_job_detail(job_id: int) -> dict:
    """search_jobs에서 받은 DB id로 공고 상세와 필수·우대 자격을 조회한다."""
    if job_id < 1:
        return _failure("INVALID_INPUT", "공고 DB id는 1 이상이어야 합니다.")

    try:
        with closing(connect_db()) as db:
            row = db.execute(
                """
                SELECT id, source, board_id, source_job_id, company, title,
                       deadline, job_url, description_html,
                       required_qualifications, preferred_qualifications
                FROM job_posting
                WHERE id = ?
                """,
                (job_id,),
            ).fetchone()

        if row is None:
            return _failure("NO_RESULT", "해당 공고가 없습니다.")

        job = dict(row)
        fields = ("required_qualifications", "preferred_qualifications")
        for field in fields:
            if job[field] is not None:
                job[field] = json.loads(job[field])

        if job["source"] == "alio" and all(job[field] is None for field in fields):
            detail = alio.fetch_job_detail(job["source_job_id"])
            job["required_qualifications"] = alio._qualifications(detail, "aplyQlfcCn")
            job["preferred_qualifications"] = alio._qualifications(detail, "prefCondCn")

            values = [
                json.dumps(job[field], ensure_ascii=False)
                if job[field] is not None
                else None
                for field in fields
            ]

            with closing(connect_db()) as db, db:
                db.execute(
                    """
                    UPDATE job_posting
                    SET required_qualifications = COALESCE(required_qualifications, ?),
                        preferred_qualifications = COALESCE(preferred_qualifications, ?)
                    WHERE id = ?
                    """,
                    (*values, job_id),
                )
        elif job['source'] in ATS_SOURCES and not job['description_html']:
            module=greenhouse if job['source']=='greenhouse' else lever
            raw=module.fetch_job_detail(job['board_id'],job['source_job_id'])
            detail=module.normalize_job(job['board_id'],raw,job['company'])
            job['description_html']=detail['description_html']
            with closing(connect_db()) as db,db:
                db.execute('UPDATE job_posting SET description_html=? WHERE id=?',(job['description_html'],job_id))

    except (alio.AlioError,AtsError) as error:
        return _failure(error.code, str(error))
    except (sqlite3.Error, json.JSONDecodeError):
        return _failure("DATABASE_ERROR", "공고 상세 정보를 처리하지 못했습니다.")
    except (AttributeError,KeyError,TypeError,ValueError):
        return _failure('UPSTREAM_INVALID_RESPONSE','공고 상세 응답 형식을 확인하지 못했습니다.')

    return {"ok": True, "data": job}


if __name__ == "__main__":
    from dotenv import load_dotenv

    from backend.app.db.database import PROJECT_ROOT

    load_dotenv(PROJECT_ROOT / ".env")

    result = search_jobs.invoke(
        {"keyword": "약사", "region": "강원", "employment_type": "정규직"}
    )
    if not result["ok"]:
        raise SystemExit(result["error"])

    print(f"search_jobs 성공 | {result['data']['count']}건")

    job = result["data"]["jobs"][0]
    detail = get_job_detail.invoke({"job_id": job["id"]})
    if not detail["ok"]:
        raise SystemExit(detail["error"])

    print(f"get_job_detail 성공 | {detail['data']['title']}")
    print("필수 자격 필드:", detail["data"]["required_qualifications"] is not None)
