"""Contracts for public ATS GET endpoints. Application submission is not used."""
import re
from datetime import date, datetime, timezone, timedelta
from html import unescape
import requests

class AtsError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message); self.code = code

def board_token(value):
    if not isinstance(value,str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}",value):
        raise AtsError("INVALID_INPUT","채용 게시판 식별자를 확인해 주세요.")
    return value

def request_json(url,params=None):
    try:response=requests.get(url,params=params or {},timeout=(5,20),allow_redirects=False)
    except requests.Timeout:raise AtsError("UPSTREAM_TIMEOUT","채용 API 응답 시간이 초과됐습니다.") from None
    except requests.RequestException:raise AtsError("UPSTREAM_ERROR","채용 API에 연결하지 못했습니다.") from None
    if response.status_code!=200:raise AtsError("UPSTREAM_ERROR",f"채용 API HTTP 상태 {response.status_code}")
    try:return response.json()
    except ValueError:raise AtsError("UPSTREAM_INVALID_RESPONSE","채용 API JSON 형식을 확인하지 못했습니다.") from None

def as_list(value):
    if value is None:return None
    if isinstance(value,str):return [value.strip()] if value.strip() else None
    if isinstance(value,list) and all(isinstance(x,str) for x in value):return [x.strip() for x in value if x.strip()] or None
    raise AtsError("UPSTREAM_INVALID_RESPONSE","채용 API의 분류 필드를 확인하지 못했습니다.")

def deadline(value):
    if not isinstance(value,str):return None
    try:return date.fromisoformat(value).isoformat()
    except ValueError:pass
    try:
        stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
        return stamp.astimezone(timezone(timedelta(hours=9))).date().isoformat() if stamp.tzinfo else None
    except ValueError:return None

def posting(source,board_id,job_id,company,title,locations,employment,url,content,due=None):
    if not isinstance(title,str) or not title.strip() or not company or job_id is None:
        raise AtsError("UPSTREAM_INVALID_RESPONSE","채용 공고 식별자·기업명·제목이 빠져 있습니다.")
    types=as_list(employment)
    # Full-time denotes working hours, not permanent employment.
    if types:types=[{"Regular":"정규직","Permanent":"정규직"}.get(x,x) for x in types]
    return {"source":source,"board_id":board_id,"source_job_id":str(job_id),"company":company,
            "title":title.strip(),"location":as_list(locations),"employment_type":types,"career_type":None,
            "ncs_categories":None,"deadline":deadline(due),"is_open":1,"job_url":url,
            "description_html":unescape(content) if isinstance(content,str) and content.strip() else None,
            "required_qualifications":None,"preferred_qualifications":None,
            "fetched_at":datetime.now(timezone.utc).isoformat(timespec='microseconds')}
