"""RSS source and explicit user-evidence evaluation; isolated DB, no network."""

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

from backend.app import career_agent
from backend.app.chat_api import get_chat_service
from backend.app.chat_service import ChatService
from backend.app.db import database
from backend.app.execution_events import EventBus
from backend.app.integrations import alio, incruit_rss as rss
from backend.app.logging_agent import LoggingAgent
from backend.app.main import app
from backend.app.match_service import ExtractedCriteria
from backend.app.tools import job_tools
from backend.app.workspace_service import WorkspaceService
from test_execution_logging import FakeModel, FakeTool, found


def xml(job_id='12345', deadline='11/23(월)', region='|서울>송파구'):
    return (f'<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel><title>RSS</title>'
            f'<item><title><![CDATA[[예시기업] Python 개발자]]></title><author>예시기업</author>'
            f'<link>https://job.incruit.com/jobdb_info/jobpost.asp?job={job_id}</link>'
            f'<description><![CDATA[▨ 지역 : {region}<br><br>▨ 경력 : 신입/경력<br>▨ 마감일 : {deadline}]]></description>'
            '<pubDate>Fri, 20 Nov 2026 10:11:01 +0900</pubDate></item></channel></rss>').encode()


def posting(job_id='12345', **changes):
    stamp = datetime.now(timezone.utc).isoformat(timespec='microseconds')
    return {**rss.parse_feed(xml(job_id), stamp)[0], **changes}


class CriteriaModel:
    def __init__(self, **changes):
        self.values = dict(required_skills=['Python'], preferred_skills=['SQL'], requirements_checked=True, **changes)
        self.inputs = []

    def with_structured_output(self, schema, **kwargs):
        if schema is not ExtractedCriteria:
            raise AssertionError('Submission must not run normal intent analysis')
        def invoke(messages):
            self.inputs.append(messages)
            return ExtractedCriteria.model_validate(self.values)
        return SimpleNamespace(invoke=invoke)


class FeedTests(unittest.TestCase):
    def test_utf8_region_and_unknown_fields(self):
        item = posting()
        self.assertEqual(item['location'], ['서울', '송파구'])
        self.assertEqual(item['company'], '예시기업')
        for key in ('deadline', 'is_open', 'employment_type', 'required_qualifications'):
            self.assertIsNone(item[key])

    def test_full_date_preserved_without_assuming_open(self):
        item = rss.parse_feed(xml(deadline='2099-01-01'), 'stamp')[0]
        self.assertEqual(item['deadline'], '2099-01-01')
        self.assertIsNone(item['is_open'])

    def test_hiring_until_filled_is_not_invented_date(self):
        self.assertIsNone(rss.parse_feed(xml(deadline='채용시'), 'stamp')[0]['deadline'])

    def test_bad_xml_html_and_entities_rejected(self):
        for content in (b'<html/>', b'<rss>', b'<!DOCTYPE rss><rss><channel/></rss>', b'<!ENTITY x "y">'):
            with self.subTest(content=content), self.assertRaises(rss.RssError):
                rss.parse_feed(content, 'stamp')

    def test_untrusted_link_rejected(self):
        with self.assertRaises(rss.RssError):
            rss.parse_feed(xml().replace(b'job.incruit.com', b'evil.example'), 'stamp')

    def test_collection_deduplicates_across_categories(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.status_code = 200
        response.iter_content.side_effect = lambda size: [xml()]
        with patch.object(rss.requests, 'get', return_value=response) as get:
            items = rss.collect_jobs()
        self.assertEqual(get.call_count, 3)
        self.assertEqual(len(items), 1)
        self.assertFalse(get.call_args.kwargs['allow_redirects'])

    def test_upstream_timeout_is_explicit(self):
        with patch.object(rss.requests, 'get', side_effect=rss.requests.Timeout('private')):
            with self.assertRaises(rss.RssError) as result:
                rss.collect_jobs()
        self.assertEqual(result.exception.code, 'UPSTREAM_TIMEOUT')
        self.assertNotIn('private', str(result.exception))


class ExtensionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name)/'extension.sqlite3'
        p = patch.object(database, 'DB_PATH', self.path)
        p.start(); self.addCleanup(p.stop)
        database.init_db()
        with closing(database.connect_db()) as db, db:
            self.profile = db.execute("INSERT INTO user_profile(desired_role,skills,career_months) VALUES('Python 개발자','[\"Python\"]',0)").lastrowid
            self.job_id = alio.upsert_job(db, posting())
            self.alio_id = alio.upsert_job(db, {**posting('77777'), 'source':'alio','board_id':'alio','is_open':1})
        self.workspace = WorkspaceService()
        self.bus = EventBus(); self.events=[]
        self.bus.subscribe(self.events.append)
        self.bus.subscribe(LoggingAgent())
        self.service = ChatService(model_factory=CriteriaModel, workspace=self.workspace, bus=self.bus)
        app.dependency_overrides[get_chat_service] = lambda: self.service
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app, raise_server_exceptions=False)
        self.client.__enter__(); self.addCleanup(self.client.__exit__, None, None, None)

    def request(self, **changes):
        return self.client.post('/api/chat', json={
            'message':'필수: Python 활용 가능. 우대: SQL 경험.', 'session_id':'rss-evidence',
            'profile_id':self.profile, 'requirements_job_id':self.job_id,
            'original_confirmed':True, 'recruitment_period_status':'unknown', **changes})

    def refresh(self, jobs):
        with patch.object(job_tools.incruit_rss, 'collect_jobs', return_value=jobs):
            return job_tools.search_jobs.invoke({'source':'incruit'})

    def test_rss_search_does_not_call_alio(self):
        with patch.object(job_tools, 'collect_open_jobs', side_effect=AssertionError('ALIO')):
            result = self.refresh([posting()])
        self.assertTrue(result['ok'])
        self.assertEqual(result['data']['data_source'], 'rss')
        self.assertEqual(result['data']['jobs'][0]['source'], 'incruit')

    def test_cache_reused_and_includes_only_snapshot(self):
        self.refresh([posting('54321')])
        with patch.object(rss, 'collect_jobs', side_effect=AssertionError('cache expected')):
            result=job_tools.search_jobs.invoke({'source':'incruit'})
        self.assertEqual(result['data']['data_source'], 'cache')
        self.assertEqual([x['source_job_id'] for x in result['data']['jobs']], ['54321'])
        with closing(database.connect_db()) as db:
            self.assertIsNone(db.execute('SELECT is_open FROM job_posting WHERE id=?',(self.job_id,)).fetchone()[0])

    def test_empty_snapshot_does_not_resurrect_cached_jobs(self):
        first=self.refresh([])
        second=job_tools.search_jobs.invoke({'source':'incruit','refresh':False})
        self.assertEqual(first['error']['code'], 'NO_RESULT')
        self.assertEqual(second['error']['code'], 'NO_RESULT')

    def test_unverifiable_filters_are_disclosed(self):
        self.refresh([posting()])
        result=job_tools.search_jobs.invoke({'source':'incruit','employment_type':'정규직','ncs_category':'IT','refresh':False})
        self.assertTrue(result['ok'])
        self.assertEqual(result['data']['unverified_filters'], ['employment_type','ncs_category'])

    def test_alio_search_is_source_scoped(self):
        result=job_tools.search_jobs.invoke({'source':'alio','refresh':False})
        self.assertTrue(result['ok'])
        self.assertEqual([x['source'] for x in result['data']['jobs']], ['alio'])

    def test_upstream_failure_does_not_write_partial_snapshot_or_fallback(self):
        before=self.path.read_bytes()
        with patch.object(rss,'collect_jobs',side_effect=rss.RssError('UPSTREAM_ERROR','failure')):
            result=job_tools.search_jobs.invoke({'source':'incruit'})
        self.assertEqual(result['error']['code'],'UPSTREAM_ERROR')
        self.assertEqual(self.path.read_bytes(),before)

    def test_search_never_automatically_scores_rss(self):
        self.service.model_factory=lambda:FakeModel([{'intents':['search_jobs'],'keyword':'Python'}])
        with patch.dict(career_agent.TOOLS,search_jobs=FakeTool(lambda args:found([{**posting(),'id':self.job_id}]))):
            with patch.object(career_agent,'score_posting',side_effect=AssertionError('must not score')):
                response=self.client.post('/api/chat',json={'message':'Python 공고','profile_id':self.profile,'job_source':'incruit'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()['matches'][0]['requires_user_requirements'])
        self.assertIn('원문',response.json()['answer'])

    def test_confirmation_profile_and_target_required(self):
        for changes,code in (({'original_confirmed':False},'ORIGINAL_CONFIRMATION_REQUIRED'),
                             ({'profile_id':None},'PROFILE_REQUIRED'),
                             ({'requirements_job_id':self.alio_id},'INVALID_JOB_SOURCE'),
                             ({'requirements_job_id':99999},'JOB_NOT_FOUND')):
            with self.subTest(code=code):
                response=self.request(**changes)
                self.assertEqual(response.json()['error']['code'],code)

    def test_input_evaluation_is_transient_and_partial_remains_unknown(self):
        response=self.request()
        self.assertEqual(response.status_code,200,response.text)
        data=response.json(); match=data['matches'][0]
        self.assertEqual(match['evidence_source'],'user_submitted')
        self.assertFalse(match['saved'])
        self.assertIn('공고의 필수조건 확인 필요',match['current']['unknown'])
        self.assertEqual(data['tool_calls'],[])
        with closing(database.connect_db()) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM match_result').fetchone()[0],0)
            self.assertIsNone(db.execute('SELECT required_qualifications FROM job_posting WHERE id=?',(self.job_id,)).fetchone()[0])

    def test_complete_input_does_not_claim_verified_open(self):
        response=self.request(requirements_complete=True)
        self.assertEqual(response.status_code,200,response.text)
        current=response.json()['matches'][0]['current']
        self.assertNotIn('공고의 필수조건 확인 필요',current['unknown'])
        self.assertIn('채용 진행 상태 또는 마감일 확인 필요',current['unknown'])
        self.assertEqual(current['eligibility'],'unknown')

    def test_raw_input_never_enters_events_or_context(self):
        text='필수: Python. 우대: SQL. private-key-12345 test@example.com'
        response=self.request(message=text)
        self.assertEqual(response.status_code,200,response.text)
        for event in self.events:
            self.assertNotIn('private-key-12345',event.payload)
            self.assertNotIn('test@example.com',event.payload)
        self.assertEqual(self.service._contexts,{})
        self.assertEqual([e.event_type for e in self.events][0],'run_started')
        self.assertEqual(self.events[-1].event_type,'run_finished')

    def test_no_conditions_asks_for_more_and_closes_failed_run(self):
        model=CriteriaModel();model.values={'requirements_checked':False}
        self.service.model_factory=lambda:model
        response=self.request(message='안녕하세요')
        self.assertEqual(response.status_code,422,response.text)
        self.assertEqual(response.json()['error']['code'],'NO_EVALUABLE_REQUIREMENTS')
        self.assertEqual(response.json()['events'][-1]['payload']['status'],'failed')

    def test_llm_failure_keeps_public_error_and_logs(self):
        model=CriteriaModel()
        model.with_structured_output=lambda *a,**k:(_ for _ in ()).throw(RuntimeError('SECRET'))
        self.service.model_factory=lambda:model
        response=self.request()
        self.assertEqual(response.status_code,502,response.text)
        self.assertNotIn('SECRET',response.text)
        self.assertTrue(response.json()['events'])

    def test_logging_failure_does_not_interrupt_evaluation(self):
        self.bus.subscribe(lambda event:(_ for _ in ()).throw(sqlite3.OperationalError('private')))
        response=self.request()
        self.assertEqual(response.status_code,200,response.text)
        self.assertGreater(self.bus.failure_count,0)

    def test_consecutive_submissions_share_session_not_run(self):
        first=self.request().json();second=self.request().json()
        self.assertEqual(first['session_id'],second['session_id'])
        self.assertNotEqual(first['run_id'],second['run_id'])

    def test_unknown_source_is_rejected(self):
        self.assertEqual(self.request(job_source='untrusted').status_code,422)

    def test_legacy_database_lazily_adds_snapshot_metadata(self):
        with closing(database.connect_db()) as db, db:
            db.execute('DROP TABLE job_source_snapshot')
        self.assertTrue(job_tools.search_jobs.invoke({'refresh':False})['ok'])
        self.assertTrue(self.refresh([posting()])['ok'])

    def test_unverified_filters_and_availability_are_logged(self):
        self.service.model_factory=lambda:FakeModel([{'intents':['search_jobs'],'employment_type':'정규직'}])
        with patch.dict(career_agent.TOOLS,search_jobs=FakeTool(lambda args:found([{**posting(),'id':self.job_id}]))):
            response=self.client.post('/api/chat',json={'message':'정규직 공고','job_source':'incruit','assess_fit':False})
        self.assertEqual(response.status_code,200,response.text)
        summary=response.json()['search_summary']
        self.assertEqual(summary['filter_status'],'unverified')
        self.assertEqual(summary['availability_status'],'unknown')
        events=[e['payload'] for e in response.json()['events'] if e['event_type']=='constraint_decided']
        self.assertEqual(events[0]['filter_status'],'unverified')

    def test_source_switch_does_not_inherit_other_source_conditions(self):
        self.service.model_factory=lambda:FakeModel([{'intents':['search_jobs'],'keyword':'Python','region':'서울','is_follow_up':True}])
        tool=FakeTool(lambda args:found([{**posting(),'id':self.job_id}]))
        with patch.dict(career_agent.TOOLS,search_jobs=tool):
            self.service.invoke('switch','서울 Python',assess_fit=False,job_source='alio')
            self.service.model_factory=lambda:FakeModel([{'intents':['search_jobs'],'is_follow_up':True}])
            result=self.service.invoke('switch','이번엔 인크루트',assess_fit=False,job_source='incruit')
        self.assertFalse(result['used_previous_context'])
        self.assertNotIn('region',result['analysis'])


if __name__ == '__main__':
    unittest.main()
