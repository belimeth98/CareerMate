"""Domestic ATS collection and Incruit-priority deduplication, no real network."""
import json,os,sqlite3,tempfile,unittest
from pathlib import Path
from contextlib import closing
from unittest.mock import patch,MagicMock
from datetime import datetime,timezone
from backend.app.db import database
from backend.app.integrations import greenhouse as gh,lever,alio,incruit_rss
from backend.app.integrations.ats_common import AtsError,posting
from backend.app import ats_sources,job_dedup,career_agent
from backend.app.tools import job_tools
from backend.app.execution_events import EventBus,EventPublisher
from test_incruit_extension import posting as rss_posting

CONFIG={'greenhouse':[{'board_id':'krboard','company':'한국기업','country':'KR','aliases':['Example Korea Inc.']}],
        'lever':[{'board_id':'parent','company':'국내사업부','country':'KR','department':'Korea','aliases':['Korea Unit']}]}
def green(job_id=1,title='Backend Engineer',location='Seoul, South Korea'):
    return {'id':job_id,'title':title,'company_name':'Provider Group','location':{'name':location},'content':'<h2>Requirements</h2><p>Python SQL</p>',
            'absolute_url':f'https://job-boards.greenhouse.io/krboard/jobs/{job_id}','metadata':[{'name':'Employment Type','value':'Regular'}]}
def lev(job_id='kr-1',department='Korea'):
    return {'id':job_id,'text':'Backend Engineer','categories':{'department':department,'location':'Seoul, South Korea','allLocations':['Seoul, South Korea'],'commitment':'Full-time'},
            'description':'<p>Python</p>','hostedUrl':f'https://jobs.lever.co/parent/{job_id}'}

class ProviderTests(unittest.TestCase):
    def test_greenhouse_requests_content_and_all_jobs(self):
        with patch.object(gh,'request_json',return_value={'jobs':[green()]}) as call:
            self.assertEqual(len(gh.fetch_jobs('krboard')),1)
        self.assertEqual(call.call_args.args[1],{'content':'true'})
    def test_invalid_board_never_requested(self):
        with patch.object(gh,'request_json') as call,self.assertRaises(AtsError):gh.fetch_jobs('../bad')
        call.assert_not_called()
    def test_bad_greenhouse_envelope_rejected(self):
        with patch.object(gh,'request_json',return_value={'jobs':{}}),self.assertRaises(AtsError):gh.fetch_jobs('krboard')
    def test_greenhouse_normalizes_full_db_contract(self):
        job=gh.normalize_job('krboard',green(),'한국기업')
        self.assertEqual(job['company'],'한국기업');self.assertEqual(job['location'],['Seoul, South Korea'])
        self.assertEqual(job['employment_type'],['정규직']);self.assertIsNone(job['deadline']);self.assertIsNone(job['ncs_categories'])
    def test_lever_paginates_and_passes_department(self):
        with patch.object(lever,'PAGE_SIZE',2),patch.object(lever,'request_json',side_effect=[[lev('a'),lev('b')],[lev('c')]]) as call:
            self.assertEqual(len(lever.fetch_jobs('parent',department='Korea')),3)
        self.assertEqual(call.call_args_list[1].args[1]['skip'],2);self.assertEqual(call.call_args.args[1]['department'],'Korea')
    def test_lever_repeated_page_rejected(self):
        with patch.object(lever,'PAGE_SIZE',1),patch.object(lever,'request_json',return_value=[lev()]),self.assertRaises(AtsError):lever.fetch_jobs('parent')
    def test_lever_pagination_cap_is_not_success(self):
        with patch.object(lever,'MAX_PAGES',1),patch.object(lever,'PAGE_SIZE',1),patch.object(lever,'request_json',return_value=[lev()]),self.assertRaises(AtsError):lever.fetch_jobs('parent')
    def test_full_time_not_invented_permanent_and_no_deadline(self):
        job=lever.normalize_job('parent',lev(),'국내사업부')
        self.assertEqual(job['employment_type'],['Full-time']);self.assertIsNone(job['deadline']);self.assertIsNone(job['career_type'])
    def test_detail_identifier_mismatch_rejected(self):
        with patch.object(gh,'request_json',return_value=green(2)),self.assertRaises(AtsError):gh.fetch_job_detail('krboard',1)
        with patch.object(lever,'request_json',return_value=lev('other')),self.assertRaises(AtsError):lever.fetch_job_detail('parent','kr-1')
    def test_http_failure_safe_and_redirect_not_followed(self):
        from backend.app.integrations import ats_common
        response=MagicMock(status_code=302)
        with patch.object(ats_common.requests,'get',return_value=response) as call,self.assertRaises(AtsError):gh.fetch_jobs('krboard')
        self.assertFalse(call.call_args.kwargs['allow_redirects'])
    def test_timeout_safe(self):
        from backend.app.integrations import ats_common
        with patch.object(ats_common.requests,'get',side_effect=ats_common.requests.Timeout('private-key')):
            with self.assertRaises(AtsError) as error:gh.fetch_jobs('krboard')
        self.assertNotIn('private-key',str(error.exception));self.assertEqual(error.exception.code,'UPSTREAM_TIMEOUT')

class DedupIdentityTests(unittest.TestCase):
    def setUp(self):
        self.ats={'source':'greenhouse','source_job_id':'1','company':'한국기업','title':'Backend Engineer','location':['Seoul, South Korea'],'job_url':'https://job-boards.greenhouse.io/krboard/jobs/1'}
        self.rss={'company':'Example Korea Inc.','title':'[Example Korea Inc.] Backend Engineer 채용','location':['서울'],'job_url':'https://job.incruit.com/jobdb_info/jobpost.asp?job=12345'}
        self.aliases={job_dedup.company_key('Example Korea Inc.'):job_dedup.company_key('한국기업')}
    def test_alias_company_title_and_city(self):self.assertTrue(job_dedup.same_posting(self.ats,self.rss,self.aliases))
    def test_same_title_different_company_kept(self):
        self.rss['company']='다른 기업';self.assertFalse(job_dedup.same_posting(self.ats,self.rss,self.aliases))
    def test_different_seniority_kept(self):
        self.rss['title']='Senior Backend Engineer';self.assertFalse(job_dedup.same_posting(self.ats,self.rss,self.aliases))
    def test_technology_symbols_not_erased(self):
        self.ats['title']='C++ Developer';self.rss['title']='C# Developer'
        self.assertFalse(job_dedup.same_posting(self.ats,self.rss,self.aliases))
    def test_different_city_kept(self):
        self.rss['location']=['부산'];self.assertFalse(job_dedup.same_posting(self.ats,self.rss,self.aliases))
    def test_different_district_kept(self):
        self.ats['location']=['서울 강남구'];self.rss['location']=['서울','마포구'];self.assertFalse(job_dedup.same_posting(self.ats,self.rss,self.aliases))
    def test_same_canonical_url_wins(self):
        self.rss.update(company='다른 표기',title='번역된 제목',job_url='https://boards.greenhouse.io/krboard/jobs/1?utm_source=incruit')
        self.assertTrue(job_dedup.same_posting(self.ats,self.rss,self.aliases))
    def test_original_url_in_rss_text(self):
        self.rss.update(title='번역된 제목',description_html='원문 https://www.example.kr/careers?gh_jid=1')
        self.assertTrue(job_dedup.same_posting(self.ats,self.rss,self.aliases))
    def test_homepage_not_a_job_identifier(self):
        self.ats.update(job_url='https://example.com/careers');self.rss.update(job_url='https://example.com/careers',title='Senior Role')
        self.assertFalse(job_dedup.same_posting(self.ats,self.rss,self.aliases))
    def test_different_job_query_id_not_deduplicated(self):
        self.ats['job_url']='https://example.com/careers?gh_jid=1';self.rss.update(job_url='https://example.com/careers?gh_jid=2',title='Senior Role')
        self.assertFalse(job_dedup.same_posting(self.ats,self.rss,self.aliases))
    def test_korean_english_region_alias(self):
        self.assertTrue(job_dedup.region_matches('서울',['Seoul, South Korea']))
        self.assertFalse(job_dedup.region_matches('부산',['Seoul, South Korea']))
    def test_gyeonggi_city_and_district_aliases(self):
        self.assertTrue(job_dedup.region_matches('경기도',['Pangyo, South Korea']))
        self.assertTrue(job_dedup.region_matches('Seongnam',['성남']))
        self.assertTrue(job_dedup.same_location(['Seongnam, South Korea'],['경기']))
        self.assertFalse(job_dedup.same_location(['판교'],['분당']))

class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.folder=Path(self.temp.name)
        self.config=self.folder/'boards.json';self.config.write_text(json.dumps(CONFIG),encoding='utf-8')
        p=patch.dict(os.environ,CAREERMATE_JOB_BOARDS_FILE=str(self.config));p.start();self.addCleanup(p.stop)
        p=patch.object(database,'DB_PATH',self.folder/'test.sqlite3');p.start();self.addCleanup(p.stop);database.init_db()
        self.rss=rss_posting('12345',company='Example Korea Inc.',title='[Example Korea Inc.] Backend Engineer 채용')
    def seed_rss(self,items=None):
        items=[self.rss] if items is None else items
        with closing(database.connect_db()) as db,db:
            ids=[alio.upsert_job(db,j) for j in items]
            for j,i in zip(items,ids):db.execute('UPDATE job_posting SET description_html=? WHERE id=?',(j['description_html'],i))
            db.execute("INSERT OR REPLACE INTO job_source_snapshot VALUES('incruit',?,?,?)",(datetime.now(timezone.utc).isoformat(),len(ids),json.dumps(ids)))
        return ids
    def test_foreign_company_config_rejected(self):
        self.config.write_text(json.dumps({'greenhouse':[dict(CONFIG['greenhouse'][0],country='US')]}),encoding='utf-8')
        with self.assertRaises(AtsError):ats_sources.configuration()
    def test_lever_shared_parent_is_locally_filtered(self):
        with patch.object(lever,'fetch_jobs',return_value=[lev('kr'),lev('us','US Business')]):ids,status=ats_sources.collect('lever',True)
        with closing(database.connect_db()) as db:self.assertEqual([r[0] for r in db.execute("SELECT source_job_id FROM job_posting")],['kr'])
        self.assertEqual(len(ids),1)
    def test_greenhouse_group_metadata_restricts_company(self):
        config=json.loads(json.dumps(CONFIG));config['greenhouse'][0]['metadata_filters']={'Corp./Studios':['KR Corp']}
        self.config.write_text(json.dumps(config),encoding='utf-8')
        a=green();a['metadata'].append({'name':'Corp./Studios','value':'KR Corp'})
        b=green(2);b['metadata'].append({'name':'Corp./Studios','value':'Foreign Studio'})
        with patch.object(gh,'fetch_jobs',return_value=[a,b]):ids,_=ats_sources.collect('greenhouse',True)
        self.assertEqual(len(ids),1)
    def test_cache_and_replan_do_not_call_provider_again(self):
        with patch.object(gh,'fetch_jobs',return_value=[green()]) as call:
            first,_=ats_sources.collect('greenhouse',True);second,_=ats_sources.collect('greenhouse',True);third,_=ats_sources.collect('greenhouse',False)
        self.assertEqual(call.call_count,1);self.assertEqual(first,second);self.assertEqual(first,third)
    def test_provider_failure_preserves_saved_jobs_without_serving_stale(self):
        with patch.object(gh,'fetch_jobs',return_value=[green()]):ids,_=ats_sources.collect('greenhouse',True)
        with closing(database.connect_db()) as db,db:db.execute("UPDATE job_source_snapshot SET fetched_at='2000-01-01T00:00:00+00:00'")
        with patch.object(gh,'fetch_jobs',side_effect=AtsError('UPSTREAM_ERROR','failure')):returned,status=ats_sources.collect('greenhouse',True)
        self.assertEqual(returned,[]);self.assertEqual(status[0]['status'],'error')
        with closing(database.connect_db()) as db:self.assertIsNotNone(db.execute('SELECT id FROM job_posting WHERE id=?',(ids[0],)).fetchone())
        cached,cached_status=ats_sources.collect('greenhouse',False)
        self.assertEqual(cached,[]);self.assertEqual(cached_status[0]['status'],'error')
    def test_successful_empty_snapshot_closes_only_matching_board(self):
        with patch.object(gh,'fetch_jobs',return_value=[green()]):ids,_=ats_sources.collect('greenhouse',True)
        with closing(database.connect_db()) as db,db:
            db.execute("UPDATE job_source_snapshot SET fetched_at='2000-01-01T00:00:00+00:00'")
            other=alio.upsert_job(db,dict(self.rss,source='alio',board_id='alio',is_open=1))
        with patch.object(gh,'fetch_jobs',return_value=[]):ats_sources.collect('greenhouse',True)
        with closing(database.connect_db()) as db:
            self.assertEqual(db.execute('SELECT is_open FROM job_posting WHERE id=?',(ids[0],)).fetchone()[0],0)
            self.assertEqual(db.execute('SELECT is_open FROM job_posting WHERE id=?',(other,)).fetchone()[0],1)
    def test_direct_source_dedup_before_limit_backfills(self):
        self.seed_rss()
        with patch.object(gh,'fetch_jobs',return_value=[green(),green(2,'Software Engineer')]):
            result=job_tools.search_jobs.invoke({'source':'greenhouse','keyword':'IT','limit':1})
        self.assertTrue(result['ok']);self.assertEqual(result['data']['duplicate_count'],1)
        self.assertEqual(result['data']['jobs'][0]['source_job_id'],'2')
    def test_all_duplicate_returns_normal_zero(self):
        self.seed_rss()
        with patch.object(gh,'fetch_jobs',return_value=[green()]):result=job_tools.search_jobs.invoke({'source':'greenhouse','keyword':'IT'})
        self.assertEqual(result['error']['code'],'NO_RESULT');self.assertEqual(result['duplicate_count'],1)
    def test_rss_reference_is_not_limited_to_requested_results(self):
        self.seed_rss([dict(self.rss,source_job_id='77777',title='다른 직무'),self.rss])
        with patch.object(gh,'fetch_jobs',return_value=[green()]):result=job_tools.search_jobs.invoke({'source':'greenhouse','limit':1})
        self.assertEqual(result['error']['code'],'NO_RESULT')
    def test_rss_failure_withholds_ats(self):
        with patch.object(incruit_rss,'collect_jobs',side_effect=incruit_rss.RssError('UPSTREAM_ERROR','rss failed')),patch.object(gh,'fetch_jobs',return_value=[green()]):
            result=job_tools.search_jobs.invoke({'source':'greenhouse'})
        self.assertEqual(result['error']['code'],'DEDUP_REFERENCE_UNAVAILABLE')
    def test_successful_empty_rss_allows_unique_ats(self):
        with patch.object(incruit_rss,'collect_jobs',return_value=[]),patch.object(gh,'fetch_jobs',return_value=[green()]):result=job_tools.search_jobs.invoke({'source':'greenhouse'})
        self.assertTrue(result['ok'])
    def test_lever_fulltime_filter_is_explicitly_unverified(self):
        self.seed_rss([])
        with patch.object(lever,'fetch_jobs',return_value=[lev()]):result=job_tools.search_jobs.invoke({'source':'lever','region':'서울','employment_type':'정규직','career_type':'신입'})
        self.assertTrue(result['ok']);job=result['data']['jobs'][0]
        self.assertEqual(job['employment_type'],['Full-time']);self.assertEqual(job['unverified_filters'],['employment_type','career_type'])
    def test_cached_detail_body_and_unknown_qualifications(self):
        self.seed_rss([])
        with patch.object(gh,'fetch_jobs',return_value=[green()]):result=job_tools.search_jobs.invoke({'source':'greenhouse'})
        job_id=result['data']['jobs'][0]['id']
        with patch.object(gh,'fetch_job_detail') as call:detail=job_tools.get_job_detail.invoke({'job_id':job_id})
        call.assert_not_called();self.assertIn('Python',detail['data']['description_html']);self.assertIsNone(detail['data']['required_qualifications'])
    def test_four_sources_and_incruit_copy_wins(self):
        alio_job=dict(self.rss,source='alio',board_id='alio',source_job_id='alio',is_open=1,title='전산 담당')
        with patch.object(job_tools,'collect_open_jobs',return_value=[alio_job]),patch.object(incruit_rss,'collect_jobs',return_value=[self.rss]),patch.object(gh,'fetch_jobs',return_value=[green(),green(2,'Software Engineer')]),patch.object(lever,'fetch_jobs',return_value=[lev()]):
            result=job_tools.search_jobs.invoke({'source':'all','keyword':'IT','limit':10})
        self.assertTrue(result['ok']);self.assertEqual(len(result['data']['source_results']),4)
        self.assertEqual(result['data']['duplicate_count'],1)
        self.assertTrue(any(j['source']=='incruit' for j in result['data']['jobs']))
        self.assertFalse(any(j['source']=='greenhouse' and j['source_job_id']=='1' for j in result['data']['jobs']))
    def test_source_event_whitelist_preserves_four_and_scrubs_free_text(self):
        events=[];bus=EventBus();bus.subscribe(events.append);publisher=EventPublisher(bus,'ats-test')
        publisher.emit('tool_finished',tool='search_jobs',source='greenhouse',duplicate_count=2,source_results=[{'source':s,'status':'ok','secret':'do-not-publish'} for s in ('alio','incruit','greenhouse','lever')])
        payload=json.loads(events[0].payload);self.assertEqual(len(payload['source_results']),4);self.assertNotIn('do-not-publish',events[0].payload)
        self.assertEqual(payload['duplicate_count'],2)

if __name__=='__main__':unittest.main()
