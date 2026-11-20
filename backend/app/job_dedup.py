"""Conservative cross-source identity matching. Incruit wins over ATS copies."""
import json,re,unicodedata
from contextlib import closing
from datetime import datetime
from urllib.parse import urlparse,parse_qs,unquote
from backend.app.db.database import connect_db
from backend.app.db.job_search import KST
from backend.app.ats_sources import configuration
from backend.app.integrations.ats_common import AtsError

REGIONS={
    '서울':('서울','seoul'), '부산':('부산','busan','pusan'), '대전':('대전','daejeon'),
    '인천':('인천','incheon'), '대구':('대구','daegu'), '광주':('광주','gwangju'), '울산':('울산','ulsan'),
    '경기':('경기','gyeonggi','성남','seongnam','판교','pangyo','분당','bundang'),
    '성남':('성남','seongnam'), '판교':('판교','pangyo'), '분당':('분당','bundang')}

def compact(value):return ''.join(c for c in unicodedata.normalize('NFKC',value or '').casefold() if c.isalnum())

def region_matches(region,locations):
    query=compact(region)
    query={'서울특별시':'서울','부산광역시':'부산','경기도':'경기'}.get(query,query)
    variants=next((v for key,v in REGIONS.items() if query in {compact(key),*(compact(x) for x in (v[:2] if key=='경기' else v))}),None)
    variants=variants or REGIONS.get(query,(region,))
    return any(compact(part) in compact(location) for part in variants for location in (locations or []))

def company_key(name):
    value=unicodedata.normalize('NFKC',name or '').casefold()
    value=re.sub(r'주식회사|\(주\)|㈜|\b(?:incorporated|inc|llc|limited|ltd|corporation|corp)\b','',value)
    return compact(value)

def company_aliases():
    mapping={}
    for boards in configuration().values():
        if not isinstance(boards,list):continue
        for board in boards:
            canonical=company_key(board['company'])
            for name in [board['company'],*board.get('aliases',[])]:mapping[company_key(name)]=canonical
    return mapping

def title_key(job,aliases):
    text=unicodedata.normalize('NFKC',job.get('title') or '').strip()
    company=aliases.get(company_key(job.get('company')),company_key(job.get('company')))
    prefix=re.match(r'^\[([^\]]+)\]\s*',text)
    if prefix and aliases.get(company_key(prefix[1]),company_key(prefix[1]))==company:text=text[prefix.end():]
    text=re.sub(r'^\s*\[(?:채용공고|채용|모집)\]\s*','',text)
    text=re.sub(r'\s*(?:채용공고|채용|모집)\s*$','',text)
    return ''.join(c for c in text.casefold() if c.isalnum() or c in '+#')

def posting_url(value):
    if not isinstance(value,str):return None
    parsed=urlparse(value)
    if parsed.scheme not in {'https','http'} or not parsed.netloc:return None
    host=(parsed.hostname or '').casefold();path=unquote(parsed.path).rstrip('/')
    # Greenhouse URLs can use either board host; tracking parameters are ignored.
    match=re.search(r'/([^/]+)/jobs/(\d+)$',path)
    if host in {'boards.greenhouse.io','job-boards.greenhouse.io'} and match:return ('greenhouse',match[1].casefold(),match[2])
    match=re.fullmatch(r'/([^/]+)/([a-zA-Z0-9-]+)',path)
    if host in {'jobs.lever.co','jobs.eu.lever.co'} and match:return ('lever',host,match[1].casefold(),match[2])
    query=parse_qs(parsed.query)
    if not query and 'gh_jid=' in parsed.fragment:query=parse_qs(parsed.fragment.lstrip('?'))
    if query.get('gh_jid'):return ('greenhouse-id',host,query['gh_jid'][0])
    # Generic career homepages are not identifiers of individual job postings.
    if not set(query)&{'job','id','job_id','jobId','postingId','recruitId'} and not re.search(r'/(?:jobs?|careers?)/[^/]+',path,re.I):return None
    return (host,path,tuple(sorted((k,tuple(v)) for k,v in query.items() if not k.startswith('utm_') and k not in {'gh_src','source','ref'})))

def _places(locations):
    texts=[unicodedata.normalize('NFKC',s).casefold() for s in (locations or [])]
    text=' '.join(texts)
    for city,variants in REGIONS.items():
        if city=='경기':variants=variants[:2]
        for v in variants:text=text.replace(v,city)
    text=text.replace('서울특별시','서울')
    text=re.sub(r'south\s*korea|republic\s*of\s*korea|대한민국|한국','',text)
    cities={city for city in REGIONS if city in text}
    if cities-{'경기'}:cities.discard('경기')
    if cities&{'판교','분당'}:cities.discard('성남')
    district_text=text
    for city in cities:district_text=district_text.replace(city,' ')
    districts=set(re.findall(r'[가-힣]+구',district_text))
    return cities,districts,compact(text)

def same_location(a,b):
    if not a or not b:return True
    ac,ad,at=_places(a);bc,bd,bt=_places(b)
    if ac and bc:
        parents={'성남':{'경기'},'판교':{'성남','경기'},'분당':{'성남','경기'}}
        if not ac&bc and not any(a in parents.get(b,set()) or b in parents.get(a,set()) for a in ac for b in bc):return False
        return not ad or not bd or bool(ad&bd)
    return at==bt or any(compact(x)==compact(y) for x in a for y in b)

def same_posting(ats,rss,aliases):
    ats_url=posting_url(ats.get('job_url'))
    rss_urls=[rss.get('job_url')]+re.findall(r'https?://[^\s<>"\']+',rss.get('description_html') or '')
    if ats_url and any(posting_url(url)==ats_url for url in rss_urls):return True
    a=aliases.get(company_key(ats.get('company')),company_key(ats.get('company')))
    b=aliases.get(company_key(rss.get('company')),company_key(rss.get('company')))
    if a and a==b and ats.get('source')=='greenhouse' and ats.get('source_job_id'):
        for url in rss_urls:
            parsed=urlparse(url or '');query=parse_qs(parsed.query or parsed.fragment.lstrip('?'))
            if query.get('gh_jid')==[str(ats['source_job_id'])]:return True
    return bool(a and a==b and title_key(ats,aliases) and title_key(ats,aliases)==title_key(rss,aliases) and same_location(ats.get('location'),rss.get('location')))

def references():
    today=datetime.now(KST).date().isoformat()
    with closing(connect_db()) as db:
        snap=db.execute("SELECT item_count,job_ids FROM job_source_snapshot WHERE source='incruit'").fetchone()
        if snap is None:raise AtsError('DEDUP_REFERENCE_UNAVAILABLE','인크루트 중복 확인 자료가 없어 추가 출처의 노출을 보류했습니다.')
        ids=json.loads(snap[1])
        if not isinstance(ids,list) or len(ids)!=snap[0] or not all(type(i) is int and i>0 for i in ids) or len(set(ids))!=len(ids):raise ValueError('Invalid RSS reference snapshot')
        count=db.execute("SELECT count(*) FROM job_posting WHERE source='incruit' AND id IN (SELECT value FROM json_each(?))",(snap[1],)).fetchone()[0]
        if count!=len(ids):raise ValueError('Incomplete RSS reference snapshot')
        rows=db.execute("SELECT company,title,location,job_url,description_html FROM job_posting WHERE source='incruit' AND id IN (SELECT value FROM json_each(?)) AND (deadline IS NULL OR deadline>=?)",(snap[1],today)).fetchall()
    result=[]
    for row in rows:
        job=dict(row);job['location']=json.loads(job['location']) if job['location'] else None;result.append(job)
    return result

def remove_incruit_duplicates(jobs):
    refs=references();aliases=company_aliases();kept=[];removed=0
    for job in jobs:
        if any(same_posting(job,rss,aliases) for rss in refs):removed+=1
        else:kept.append(job)
    return kept,removed
