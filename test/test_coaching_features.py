"""Integrated search and preparation-first conversations; no network/production writes."""

import base64
import json
import sqlite3
import struct
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.app import career_agent
from backend.app.chat_api import get_chat_service
from backend.app.chat_service import ChatService
from backend.app.coaching_api import CoachingService, CoachOutput, CaptureDraft, checked_image, get_coaching_service
from backend.app.db import database
from backend.app.db.profile_migrations import ensure_profile_extensions
from backend.app.execution_events import EventBus
from backend.app.job_fit import JobCriteria, evaluate_match
from backend.app.logging_agent import LoggingAgent
from backend.app.main import app
from backend.app.match_service import ExtractedCriteria
from backend.app.search_support import unified_search, suggested_terms
from backend.app.tools import job_tools
from backend.app.workspace_service import WorkspaceService, AppProblem
from test_execution_logging import FakeTool, found
from test_incruit_extension import posting


PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aK4sAAAAASUVORK5CYII=')
IMAGE='data:image/png;base64,'+base64.b64encode(PNG).decode()


class Model:
    def __init__(self): self.calls=[]
    def with_structured_output(self,schema,**kwargs):
        def invoke(messages):
            self.calls.append((schema,messages))
            if schema is career_agent.RequestAnalysis:
                return schema(intents=['search_jobs'],keyword='Python',limit=1)
            if schema is ExtractedCriteria:
                return schema(required_skills=['Python'],requirements_checked=True)
            if schema is CoachOutput:
                return schema(reply='실제로 맡은 행동을 더 알려 주세요. 아직 저장하지 않은 초안입니다.',
                              draft={'title':'API 프로젝트','action':'Python으로 API 구현','skills':['Python']},missing_fields=['결과'])
            if schema is CaptureDraft:
                return schema(required='Python',preferred='SQL',recruitment_start='2026-11-01',recruitment_end='2026-12-31',
                              period_text='2026.11.01 ~ 2026.12.31',period_status='provided')
            raise AssertionError(schema)
        return SimpleNamespace(invoke=invoke)
    def invoke(self,messages):return SimpleNamespace(text='경험을 연결하고 다음 준비를 함께 정리해요.')


class SearchTests(unittest.TestCase):
    def test_two_sources_interleaved_and_limited(self):
        def invoke(args):
            return found([{'id':1,'title':'ALIO'},{'id':2,'title':'ALIO2'}] if args['source']=='alio' else [{'id':3,'title':'RSS'}])
        result=unified_search(invoke,{'limit':2})
        self.assertEqual([j['id'] for j in result['data']['jobs']],[1,3])
        self.assertFalse(result['data']['partial_failure'])

    def test_one_failure_retains_other_provider_and_marks_partial(self):
        result=unified_search(lambda args:found([{'id':1}]) if args['source']=='incruit' else
                              {'ok':False,'error':{'code':'UPSTREAM_TIMEOUT'}},{'limit':2})
        self.assertTrue(result['ok']);self.assertTrue(result['data']['partial_failure'])
        self.assertEqual(result['data']['source_results'][0]['status'],'error')

    def test_empty_plus_failure_is_not_normal_zero(self):
        result=unified_search(lambda args:{'ok':False,'error':{'code':'NO_RESULT' if args['source']=='incruit' else 'UPSTREAM_ERROR'}},{'limit':2})
        self.assertEqual(result['error']['code'],'UPSTREAM_PARTIAL_FAILURE')

    def test_two_normal_empty_results_are_zero(self):
        result=unified_search(lambda args:{'ok':False,'error':{'code':'NO_RESULT'}},{'limit':2})
        self.assertEqual(result['error']['code'],'NO_RESULT')

    def test_backend_suggests_it_without_claiming_results(self):
        self.assertIn('IT',suggested_terms('백엔드'))

    def test_image_url_and_wrong_mime_rejected(self):
        for value in ('https://example.com/image.png',IMAGE.replace('image/png','image/jpeg'),'data:image/png;base64,aaa!'):
            with self.subTest(value=value[:30]),self.assertRaises(AppProblem):checked_image(value)

    def test_image_header_limits_and_valid_png(self):
        self.assertEqual(checked_image(IMAGE)[1],len(PNG))
        huge=PNG[:16]+struct.pack('>II',10000,10000)+PNG[24:]
        with self.assertRaises(AppProblem):checked_image('data:image/png;base64,'+base64.b64encode(huge).decode())

    def test_lazy_migration_retains_old_rows(self):
        db=sqlite3.connect(':memory:')
        db.executescript("CREATE TABLE user_profile(id INTEGER PRIMARY KEY,desired_role TEXT); INSERT INTO user_profile VALUES(1,'개발자'); CREATE TABLE experience(id INTEGER PRIMARY KEY,title TEXT); INSERT INTO experience VALUES(1,'경험');")
        ensure_profile_extensions(db);ensure_profile_extensions(db)
        self.assertEqual(db.execute('SELECT desired_role FROM user_profile').fetchone()[0],'개발자')
        self.assertEqual(db.execute('SELECT skills FROM experience').fetchone()[0],'[]')
        db.close()


class PreparationTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.path=Path(temp.name)/'preparation.sqlite3'
        p=patch.object(database,'DB_PATH',self.path);p.start();self.addCleanup(p.stop);database.init_db()
        self.workspace=WorkspaceService()
        with closing(database.connect_db()) as db,db:
            self.profile=db.execute("INSERT INTO user_profile(profile_name,desired_role) VALUES('백엔드 준비','Python 개발자')").lastrowid
            self.other=db.execute("INSERT INTO user_profile(profile_name,desired_role) VALUES('다른 프로필','Python 개발자')").lastrowid
            from backend.app.integrations.alio import upsert_job
            self.job=upsert_job(db,posting())
            self.alio_job=upsert_job(db,{**posting('99999'),'source':'alio','board_id':'alio','deadline':'2099-12-31','is_open':1})
        self.bus=EventBus();self.events=[];self.bus.subscribe(self.events.append);self.bus.subscribe(LoggingAgent())
        self.model=Model();self.chat=ChatService(model_factory=lambda:self.model,workspace=self.workspace,bus=self.bus)
        self.coach=CoachingService(lambda:self.model,bus=self.bus)
        app.dependency_overrides[get_chat_service]=lambda:self.chat
        app.dependency_overrides[get_coaching_service]=lambda:self.coach
        self.addCleanup(app.dependency_overrides.clear)
        self.client=TestClient(app,raise_server_exceptions=False);self.client.__enter__();self.addCleanup(self.client.__exit__,None,None,None)

    def submit(self,**changes):
        return self.client.post('/api/chat',json={'message':'필수: Python','session_id':'prep','profile_id':self.profile,
             'requirements_job_id':self.job,'original_confirmed':True,'requirements_complete':True,
             'recruitment_period_status':'unknown',**changes})

    def follow(self,**changes):
        return self.client.post('/api/chat',json={'message':'추가 경험 없음','session_id':'prep','profile_id':self.profile,
                                                'fit_follow_up_action':'none',**changes})

    def add_experience(self,profile=None,**changes):
        response=self.client.post(f'/api/profiles/{profile or self.profile}/experiences',json={
            'title':'API 프로젝트','action':'Python으로 API와 검색 개선','result':'조회 흐름을 개선함','skills':['Python'],**changes})
        self.assertEqual(response.status_code,201,response.text);return response.json()

    def test_low_score_is_withheld_in_body_and_events(self):
        response=self.submit();self.assertEqual(response.status_code,200,response.text);data=response.json()
        self.assertTrue(data['fit_follow_up']['scores_withheld'])
        self.assertNotIn('current',data['matches'][0]);self.assertNotIn('_criteria',response.text)
        self.assertNotIn('current_score',json.dumps(data['events']))
        self.assertIn('경험',data['answer'])

    def test_additional_experience_in_main_chat_becomes_reviewable_draft(self):
        self.submit()
        response=self.client.post('/api/chat',json={'message':'Python API와 SQL로 검색을 개선한 프로젝트 경험이 있어요','session_id':'prep','profile_id':self.profile})
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()['fit_follow_up'])
        self.assertEqual(response.json()['experience_draft']['skills'],['Python'])
        self.assertNotIn('current',response.json()['matches'][0])
        self.assertEqual(self.client.get(f'/api/profiles/{self.profile}/experiences').json()['experiences'],[])

    def test_none_discloses_reference_and_preparation_advice(self):
        self.submit();response=self.follow();self.assertEqual(response.status_code,200,response.text)
        self.assertIn('current',response.json()['matches'][0]);self.assertIsNone(response.json()['fit_follow_up'])
        self.assertIn('우선 준비',response.json()['answer'])

    def test_natural_none_continues_without_llm_or_action_field(self):
        self.submit();self.chat.model_factory=lambda:(_ for _ in ()).throw(RuntimeError('no key'))
        response=self.client.post('/api/chat',json={'message':'없어요','session_id':'prep','profile_id':self.profile})
        self.assertEqual(response.status_code,200,response.text);self.assertIn('current',response.json()['matches'][0])

    def test_common_no_experience_phrases_disclose_after_question(self):
        for phrase in ('그런 경험은 없어요', '아니요, 더 이상 없어요', '추가 경험이 없습니다'):
            with self.subTest(phrase=phrase):
                self.submit()
                response=self.client.post('/api/chat',json={'message':phrase,'session_id':'prep','profile_id':self.profile})
                self.assertEqual(response.status_code,200,response.text)
                self.assertIsNone(response.json()['fit_follow_up'])
                self.assertIn('current',response.json()['matches'][0])

    def test_saved_experience_improves_recheck(self):
        self.submit();self.add_experience()
        response=self.follow(fit_follow_up_action='recheck',message='경험 반영')
        self.assertEqual(response.status_code,200,response.text)
        self.assertIsNone(response.json()['fit_follow_up']);self.assertEqual(response.json()['matches'][0]['current']['gaps'],[])

    def test_other_profile_experience_does_not_improve_recheck(self):
        self.submit();self.add_experience(self.other)
        response=self.follow(fit_follow_up_action='recheck',message='다시 확인')
        self.assertTrue(response.json()['fit_follow_up']['scores_withheld'])

    def test_pending_review_cannot_cross_profile(self):
        self.submit();self.assertEqual(self.follow(profile_id=self.other).status_code,409)

    def test_new_profile_can_atomically_include_multiple_experiences(self):
        response=self.client.post('/api/profiles',json={'profile_name':'새 준비','desired_role':'개발자','initial_experiences':[
            {'title':'웹 프로젝트','skills':['Python']},{'title':'데이터 교육','experience_type':'other','skills':['SQL']}]})
        self.assertEqual(response.status_code,201,response.text)
        profile=response.json();self.assertEqual(profile['profile_name'],'새 준비')
        self.assertEqual(len(self.client.get(f"/api/profiles/{profile['id']}/experiences").json()['experiences']),2)
        self.assertEqual(len(self.client.get('/api/profiles').json()['profiles']),3)

    def test_experience_update_delete_and_owner_scope(self):
        item=self.add_experience();url=f"/api/profiles/{self.profile}/experiences/{item['id']}"
        body={'title':'수정','skills':['SQL'],'situation':'상황','task':'과제','action':'SQL 활용','result':'결과'}
        self.assertEqual(self.client.put(url,json=body).status_code,200)
        self.assertEqual(self.client.delete(f"/api/profiles/{self.other}/experiences/{item['id']}").status_code,404)
        self.assertEqual(self.client.delete(url).status_code,200)

    def test_experience_skills_and_star_are_used_in_evaluation(self):
        self.add_experience()
        with closing(database.connect_db()) as db:
            result=evaluate_match(db,self.profile,self.job,JobCriteria(required_skills=('Python',),project_keywords=('검색 개선',)),date.today())
        self.assertEqual(result['current']['gaps'],[])
        self.assertEqual(result['current']['by_criterion']['프로젝트'],10)

    def test_invalid_experience_date_does_not_save(self):
        response=self.client.post(f'/api/profiles/{self.profile}/experiences',json={'title':'역전','start_date':'2026-12-01','end_date':'2026-01-01'})
        self.assertEqual(response.status_code,422)
        self.assertEqual(self.client.get(f'/api/profiles/{self.profile}/experiences').json()['experiences'],[])

    def test_coach_draft_is_not_saved_until_user_confirms(self):
        response=self.client.post('/api/experience-coach',json={'messages':[{'role':'user','content':'Python API를 만들었어요. private-user-secret'}]})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['draft']['skills'],['Python'])
        self.assertEqual(self.client.get(f'/api/profiles/{self.profile}/experiences').json()['experiences'],[])
        self.assertNotIn('private-user-secret',''.join(e.payload for e in self.events))

    def test_capture_reads_reviewable_draft_without_db_writes(self):
        response=self.client.post('/api/requirements/capture',json={'images':[IMAGE]})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['required'],'Python')
        self.assertEqual(response.json()['recruitment_end'],'2026-12-31')
        self.assertNotIn(IMAGE,''.join(e.payload for e in self.events))
        with closing(database.connect_db()) as db:self.assertIsNone(db.execute('SELECT required_qualifications FROM job_posting WHERE id=?',(self.job,)).fetchone()[0])

    def test_capture_rejects_remote_url_before_model(self):
        response=self.client.post('/api/requirements/capture',json={'images':['https://example.com/image.png']})
        self.assertEqual(response.status_code,422);self.assertEqual(self.model.calls,[])

    def test_image_failure_is_safe_and_manual_entry_still_works(self):
        self.coach.model_factory=lambda:(_ for _ in ()).throw(RuntimeError('secret'))
        response=self.client.post('/api/requirements/capture',json={'images':[IMAGE]})
        self.assertEqual(response.status_code,503);self.assertNotIn('secret',response.text)
        self.assertEqual(self.submit().status_code,200)

    def test_recruitment_period_must_be_explicit(self):
        self.assertEqual(self.submit(recruitment_period_status=None).status_code,422)
        self.assertEqual(self.submit(recruitment_period_status='provided').status_code,422)
        self.assertEqual(self.submit(recruitment_period_status='provided',recruitment_start='2026-12-31',recruitment_end='2026-11-01').status_code,422)

    def test_period_is_used_without_overwriting_shared_posting(self):
        response=self.submit(recruitment_period_status='provided',recruitment_start='2026-11-01',recruitment_end='2099-12-31')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['jobs'][0]['deadline'],'2099-12-31')
        with closing(database.connect_db()) as db:self.assertIsNone(db.execute('SELECT deadline FROM job_posting WHERE id=?',(self.job,)).fetchone()[0])

    def test_future_growth_plan_uses_user_provided_deadline(self):
        with closing(database.connect_db()) as db,db:
            db.execute("INSERT INTO growth_plan(user_profile_id,plan_type,target_name,expected_skills,target_date) VALUES(?,'skill','Python','[\"Python\"]','2099-01-01')",(self.profile,))
        self.submit(recruitment_period_status='provided',recruitment_start='2026-01-01',recruitment_end='2099-12-31')
        result=self.follow().json()['matches'][0]
        self.assertGreater(result['future']['score'],result['current']['score'])

    def test_search_zero_suggestions_and_no_automatic_broadening(self):
        tool=FakeTool(lambda args:{'ok':False,'error':{'code':'NO_RESULT','message':'zero'}})
        with patch.dict(career_agent.TOOLS,search_jobs=tool):
            response=self.client.post('/api/chat',json={'message':'백엔드 공고','assess_fit':False})
        self.assertEqual(response.status_code,200,response.text)
        self.assertIn('IT',response.json()['search_suggestions']);self.assertEqual(len(tool.calls),1)
        self.assertEqual(tool.calls[0]['source'],'all')

    def test_snapshot_membership_is_independent_of_timestamp_collisions(self):
        stamp='2026-11-20T00:00:00+00:00'
        from backend.app.integrations import incruit_rss
        with closing(database.connect_db()) as db,db:
            db.execute("UPDATE job_posting SET fetched_at=? WHERE source='incruit'",(stamp,))
        with patch.object(incruit_rss,'collect_jobs',return_value=[posting('33333',fetched_at=stamp)]):
            result=job_tools.search_jobs.invoke({'source':'incruit'})
        self.assertEqual([row['source_job_id'] for row in result['data']['jobs']],['33333'])

    def test_it_broad_search_includes_ncs_information_jobs(self):
        with closing(database.connect_db()) as db,db:
            db.execute("UPDATE job_posting SET title='일반직 채용',ncs_categories='[\"정보통신\"]' WHERE id=?",(self.alio_job,))
        result=job_tools.search_jobs.invoke({'keyword':'IT','source':'alio','refresh':False})
        self.assertTrue(result['ok'])

    def test_role_words_can_have_other_words_between_them(self):
        with closing(database.connect_db()) as db,db:
            db.execute("UPDATE job_posting SET title='Python SQL 개발자 채용' WHERE id=?",(self.job,))
            fit=evaluate_match(db,self.profile,self.job,JobCriteria(),date.today())
        self.assertEqual(fit['current']['by_criterion']['직무'],15)

    def test_low_alio_result_not_saved_until_experience_confirmed(self):
        with closing(database.connect_db()) as db:
            row=dict(db.execute('SELECT * FROM job_posting WHERE id=?',(self.alio_job,)).fetchone())
        row['required_qualifications']=['Python'];row['preferred_qualifications']=[]
        with patch.dict(career_agent.TOOLS,search_jobs=FakeTool(lambda args:found([row])),
                        get_job_detail=FakeTool(lambda args:{'ok':True,'data':row})):
            response=self.client.post('/api/chat',json={'message':'Python 공고','profile_id':self.profile,'session_id':'prep'})
        self.assertTrue(response.json()['fit_follow_up'])
        with closing(database.connect_db()) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM match_result').fetchone()[0],0)
        self.assertEqual(self.follow().status_code,200)
        with closing(database.connect_db()) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM match_result').fetchone()[0],1)


if __name__=='__main__':unittest.main()
