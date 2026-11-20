from backend.app.integrations.ats_common import AtsError,board_token,request_json,posting
BASE_URL="https://boards-api.greenhouse.io/v1/boards"

def fetch_jobs(board_id: str) -> list[dict]:
    data=request_json(f"{BASE_URL}/{board_token(board_id)}/jobs",{"content":"true"})
    if not isinstance(data,dict) or not isinstance(data.get('jobs'),list) or not all(isinstance(j,dict) for j in data['jobs']):
        raise AtsError("UPSTREAM_INVALID_RESPONSE","Greenhouse 공고 목록 형식을 확인하지 못했습니다.")
    return data['jobs']

def fetch_job_detail(board_id: str,job_id: int) -> dict:
    if not str(job_id).isdigit():raise AtsError("INVALID_INPUT","Greenhouse 공고 번호를 확인해 주세요.")
    data=request_json(f"{BASE_URL}/{board_token(board_id)}/jobs/{job_id}")
    if not isinstance(data,dict) or str(data.get('id'))!=str(job_id):raise AtsError("UPSTREAM_INVALID_RESPONSE","Greenhouse 상세 식별자가 일치하지 않습니다.")
    return data

def normalize_job(board_id: str,job: dict,company_name: str|None=None) -> dict:
    employment=None
    for field in job.get('metadata') or []:
        if isinstance(field,dict) and field.get('name')=='Employment Type':employment=field.get('value');break
    if type(job.get('id')) is not int or job['id']<1:raise AtsError("UPSTREAM_INVALID_RESPONSE","Greenhouse 공고 번호가 올바르지 않습니다.")
    return posting('greenhouse',board_id,job['id'],company_name or job.get('company_name'),job.get('title'),
                   (job.get('location') or {}).get('name'),employment,job.get('absolute_url'),job.get('content'),job.get('application_deadline'))
