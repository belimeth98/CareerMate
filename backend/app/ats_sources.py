"""Domestic company allowlist and complete per-board snapshots."""
import hashlib,json,os
from contextlib import closing
from datetime import datetime,timezone,timedelta
from pathlib import Path
from backend.app.db.database import PROJECT_ROOT,connect_db
from backend.app.integrations import greenhouse,lever,alio,incruit_rss
from backend.app.integrations.ats_common import AtsError,board_token

def configuration():
    path=Path(os.getenv('CAREERMATE_JOB_BOARDS_FILE') or PROJECT_ROOT/'config/job_boards.json')
    try:data=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):raise AtsError('CONFIGURATION_ERROR','국내 기업 채용 게시판 설정을 확인해 주세요.') from None
    if not isinstance(data,dict):raise AtsError('CONFIGURATION_ERROR','채용 게시판 설정은 JSON 객체여야 합니다.')
    for source in ('greenhouse','lever'):
        boards=data.get(source,[])
        if not isinstance(boards,list) or len(boards)>20:raise AtsError('CONFIGURATION_ERROR','출처별 국내 기업은 최대 20개까지 설정할 수 있습니다.')
        seen=set()
        for row in boards:
            if not isinstance(row,dict) or row.get('country')!='KR' or not isinstance(row.get('company'),str) or not row['company'].strip():raise AtsError('CONFIGURATION_ERROR','국내 기업(country=KR)의 이름과 게시판을 설정해 주세요.')
            try:board_token(row.get('board_id'))
            except AtsError:raise AtsError('CONFIGURATION_ERROR','채용 게시판 식별자를 확인해 주세요.') from None
            if not isinstance(row.get('aliases',[]),list) or not all(isinstance(s,str) and s.strip() for s in row.get('aliases',[])):raise AtsError('CONFIGURATION_ERROR','기업 별칭은 문자열 배열이어야 합니다.')
            if row.get('department') is not None and (not isinstance(row['department'],str) or not row['department'].strip()):raise AtsError('CONFIGURATION_ERROR','국내 사업부 필터를 확인해 주세요.')
            filters=row.get('metadata_filters',{})
            if not isinstance(filters,dict) or any(not isinstance(k,str) or not isinstance(v,list) or not v or not all(isinstance(s,str) for s in v) for k,v in filters.items()):raise AtsError('CONFIGURATION_ERROR','기업 메타데이터 필터를 확인해 주세요.')
            identity=(row['board_id'],row.get('department',''))
            if identity in seen:raise AtsError('CONFIGURATION_ERROR','중복된 채용 게시판 설정입니다.')
            seen.add(identity)
    return data

def snapshot_key(source,board):
    scope=json.dumps({k:board.get(k) for k in ('board_id','company','department','country','metadata_filters')},sort_keys=True)
    return f"ats:{source}:"+hashlib.sha256(scope.encode()).hexdigest()[:24]

def _snapshot(key):
    with closing(connect_db()) as db:row=db.execute('SELECT fetched_at,job_ids FROM job_source_snapshot WHERE source=?',(key,)).fetchone()
    return (row[0],json.loads(row[1])) if row else None

def collect(source,refresh):
    boards=configuration().get(source,[]);ids=[];results=[]
    with closing(connect_db()) as db:incruit_rss.ensure_snapshot_table(db)
    for board in boards:
        key=snapshot_key(source,board);snapshot=_snapshot(key);fresh=False
        if snapshot:
            try:fresh=timedelta(0)<=datetime.now(timezone.utc)-datetime.fromisoformat(snapshot[0])<timedelta(minutes=15)
            except (ValueError,TypeError):pass
        if not refresh or fresh:
            if snapshot is None:
                results.append({'board_id':board['board_id'],'status':'error','error_code':'UPSTREAM_ERROR'})
                continue
            ids.extend(snapshot[1] if snapshot else []);results.append({'board_id':board['board_id'],'status':'ok','data_source':'cache'});continue
        module=greenhouse if source=='greenhouse' else lever
        try:
            raw=module.fetch_jobs(board['board_id'],**({'department':board['department']} if source=='lever' and board.get('department') else {}))
            if board.get('department'):raw=[j for j in raw if (j.get('categories') or {}).get('department')==board['department']]
            for field,allowed in board.get('metadata_filters',{}).items():
                raw=[j for j in raw if any(isinstance(m,dict) and m.get('name')==field and m.get('value') in allowed for m in j.get('metadata') or [])]
            postings=[module.normalize_job(board['board_id'],j,board['company']) for j in raw]
            new_ids=[];stamp=datetime.now(timezone.utc).isoformat(timespec='microseconds')
            with closing(connect_db()) as db,db:
                db.execute('UPDATE job_posting SET is_open=0 WHERE source=? AND board_id=? AND company=?',(source,board['board_id'],board['company']))
                for job in postings:
                    job_id=alio.upsert_job(db,job);new_ids.append(job_id)
                    db.execute('UPDATE job_posting SET description_html=? WHERE id=?',(job['description_html'],job_id))
                db.execute('INSERT INTO job_source_snapshot(source,fetched_at,item_count,job_ids) VALUES(?,?,?,?) ON CONFLICT(source) DO UPDATE SET fetched_at=excluded.fetched_at,item_count=excluded.item_count,job_ids=excluded.job_ids',(key,stamp,len(new_ids),json.dumps(new_ids)))
            ids.extend(new_ids);results.append({'board_id':board['board_id'],'status':'ok','data_source':'external_api'})
        except (AtsError,AttributeError,KeyError,TypeError,ValueError) as error:
            with closing(connect_db()) as db,db:db.execute('DELETE FROM job_source_snapshot WHERE source=?',(key,))
            results.append({'board_id':board['board_id'],'status':'error','error_code':error.code if isinstance(error,AtsError) else 'UPSTREAM_INVALID_RESPONSE'})
    return list(dict.fromkeys(ids)),results
