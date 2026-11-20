import re
from html import escape
from backend.app.integrations.ats_common import AtsError,board_token,request_json,posting
BASE_URL="https://api.lever.co/v0/postings"
PAGE_SIZE=100
MAX_PAGES=100

def fetch_jobs(board_id: str,*,department: str|None=None) -> list[dict]:
    url=f"{BASE_URL}/{board_token(board_id)}";jobs={}
    for page in range(MAX_PAGES):
        params={"mode":"json","limit":PAGE_SIZE,"skip":page*PAGE_SIZE}
        if department:params['department']=department
        batch=request_json(url,params)
        if not isinstance(batch,list) or not all(isinstance(j,dict) and isinstance(j.get('id'),str) for j in batch):raise AtsError("UPSTREAM_INVALID_RESPONSE","Lever 공고 목록 형식을 확인하지 못했습니다.")
        before=len(jobs)
        for job in batch:jobs[job['id']]=job
        if len(batch)<PAGE_SIZE:return list(jobs.values())
        if len(jobs)==before:raise AtsError("UPSTREAM_INVALID_RESPONSE","Lever 페이지가 반복되어 전체 수집을 확인하지 못했습니다.")
    raise AtsError("UPSTREAM_INVALID_RESPONSE","Lever 수집 상한에 도달해 전체 목록을 확인하지 못했습니다.")

def fetch_job_detail(board_id: str,job_id: str) -> dict:
    if not isinstance(job_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',job_id):raise AtsError("INVALID_INPUT","Lever 공고 번호를 확인해 주세요.")
    data=request_json(f"{BASE_URL}/{board_token(board_id)}/{job_id}",{"mode":"json"})
    if not isinstance(data,dict) or data.get('id')!=job_id:raise AtsError("UPSTREAM_INVALID_RESPONSE","Lever 상세 식별자가 일치하지 않습니다.")
    return data

def normalize_job(board_id: str,job: dict,company_name: str) -> dict:
    if not isinstance(job.get('id'),str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',job['id']):raise AtsError("UPSTREAM_INVALID_RESPONSE","Lever 공고 번호가 올바르지 않습니다.")
    categories=job.get('categories') or {};parts=[job.get('description')]
    for section in job.get('lists') or []:
        if section.get('text'):parts.append(f"<h3>{escape(section['text'])}</h3>")
        if section.get('content'):parts.append(f"<ul>{section['content']}</ul>")
    parts.append(job.get('additional'))
    return posting('lever',board_id,job['id'],company_name,job.get('text'),categories.get('allLocations') or categories.get('location'),
                   categories.get('commitment'),job.get('hostedUrl'),'\n'.join(p for p in parts if p))
