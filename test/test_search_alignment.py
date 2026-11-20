"""Shared role matching and provider evidence; isolated SQLite, no network."""
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch, MagicMock

from backend.app import job_taxonomy as taxonomy
from backend.app.db import database
from backend.app.db.job_search import search_jobs
from backend.app.integrations import alio, incruit_rss as rss
from backend.app.search_support import unified_search
from backend.app.tools import job_tools
from test_incruit_extension import xml


def item(title, source="alio", **changes):
    return dict(source=source, title=title, company="검증 기관", ncs_categories=[], rss_categories=[], **changes)


class ClassificationTests(unittest.TestCase):
    def test_generic_development_is_not_it(self):
        for title in ("의료기기 연구개발", "의약품 개발", "제품개발 분야 경력", "지역 개발 사업", "인재개발 연구원"):
            with self.subTest(title=title):self.assertIsNone(taxonomy.match_job(item(title), "IT"))

    def test_ascii_it_does_not_match_recruitment_or_audit(self):
        for title in ("Recruitment Announcement for a Researcher", "Audit Specialist", "University Researcher"):
            with self.subTest(title=title):self.assertIsNone(taxonomy.match_job(item(title), "IT"))

    def test_it_word_can_touch_korean_and_punctuation(self):
        for title in ("IT운영 담당자", "IT 부문 경력", "(IT) 지원"):
            with self.subTest(title=title):self.assertEqual(taxonomy.match_job(item(title), "IT")["level"], "direct")

    def test_employer_is_not_role_evidence(self):
        job=item("연구위원 채용");job["company"]="한국개발연구원 IT센터"
        self.assertIsNone(taxonomy.match_job(job,"IT"))
        job.update(title="[(주)IT센터] 사무직 채용",company="주식회사 IT센터")
        self.assertIsNone(taxonomy.match_job(job,"IT"))

    def test_shared_categories_are_provider_independent(self):
        for title, category in [("백엔드 개발자","software"),("데이터 분석가","data_ai"),("클라우드 운영","infrastructure"),("정보보안 담당","security"),("통신설비 기사","communications")]:
            for source in ("alio","incruit"):
                with self.subTest(title=title,source=source):self.assertIn(category,taxonomy.match_job(item(title,source),"IT")["categories"])

    def test_ncs_is_related_not_verified_role(self):
        job=item("일반직 채용");job["ncs_categories"]=["정보통신","경영.회계.사무"]
        match=taxonomy.match_job(job,"IT")
        self.assertEqual(match["level"],"related");self.assertIn("여러 직무",match["reasons"][0])

    def test_employer_name_inside_title_is_not_it_role(self):
        job=item('2026년 정보통신정책연구원 연구책임자 채용');job['company']='정보통신정책연구원'
        self.assertIsNone(taxonomy.match_job(job,'IT'))

    def test_developer_role_supported_but_non_it_developer_excluded(self):
        self.assertIsNotNone(taxonomy.match_job(item('개발자 채용'),'개발자'))
        self.assertIsNone(taxonomy.match_job(item('의료기기 개발자 채용'),'IT'))

    def test_application_and_erp_operations_are_it(self):
        self.assertIn('infrastructure',taxonomy.match_job(item('어플리케이션 및 ERP 운영'),'IT')['categories'])

    def test_combined_grade_name_is_not_communications_role(self):
        self.assertIsNone(taxonomy.match_job(item('공무직(전기기계통신기사-기계, 전기분야)'),'IT'))
        self.assertIn('communications',taxonomy.match_job(item('공무직(전기기계통신기사-통신분야)'),'IT')['categories'])

    def test_ai_advertising_phrase_does_not_create_ai_role(self):
        job=item('[AI에 대체되지 않을 신사업부] 풀스택 개발자')
        self.assertIsNone(taxonomy.match_job(job,'AI'))
        self.assertEqual(taxonomy.match_job(job,'IT')['categories'],['software'])

    def test_rss_it_category_is_related_not_ncs(self):
        job=item("2026년 신입 채용","incruit");job["rss_categories"]=["150"]
        self.assertEqual(taxonomy.match_job(job,"IT")["level"],"related")
        self.assertEqual(job["ncs_categories"],[])

    def test_conflicting_feed_category_does_not_promote_pharma(self):
        job=item("제품개발 분야 경력채용","incruit");job["rss_categories"]=["150","190"]
        self.assertIsNone(taxonomy.match_job(job,"IT"))

    def test_explicit_software_evidence_survives_medical_industry(self):
        self.assertEqual(taxonomy.match_job(item("의료기기 소프트웨어 개발자"),"IT")["level"],"direct")

    def test_backend_aliases_do_not_widen_to_frontend(self):
        for query in ("백엔드","backend","Back-End"):
            with self.subTest(query=query):
                self.assertIsNotNone(taxonomy.match_job(item("백엔드 개발자"),query))
                self.assertIsNone(taxonomy.match_job(item("프론트엔드 개발자"),query))

    def test_specific_role_not_invented_from_broad_ncs_or_rss(self):
        job=item("일반직 채용","incruit");job.update(ncs_categories=["정보통신"],rss_categories=["150"])
        self.assertIsNone(taxonomy.match_job(job,"백엔드"))

    def test_non_it_queries_still_support_employer_and_multiple_terms(self):
        job=item("Python SQL 개발자");job["company"]="예시기업"
        self.assertIsNotNone(taxonomy.match_job(job,"예시기업"))
        self.assertIsNotNone(taxonomy.match_job(job,"Python SQL"))
        self.assertIsNone(taxonomy.match_job(item("JavaScript 개발자"),"Java"))

    def test_data_entry_and_financial_words_not_ai(self):
        for title in ("데이터 입력 사무원","Financial Specialist"):
            self.assertIsNone(taxonomy.match_job(item(title),"AI"))


class PersistenceAndSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'jobs.sqlite3'
        p=patch.object(database,'DB_PATH',self.path);p.start();self.addCleanup(p.stop);database.init_db()

    def seed(self,title,source="alio",ncs=None,categories=None,deadline="2099-01-01",region="서울"):
        with closing(database.connect_db()) as db,db:
            sid=str(db.execute('SELECT count(*)+10000 FROM job_posting').fetchone()[0])
            job=alio.normalize_job({'recrutPblntSn':sid,'instNm':'검증기관','recrutPbancTtl':title,'ongoingYn':'Y','pbancEndYmd':deadline.replace('-',''),'workRgnNmLst':region,'hireTypeNmLst':'정규직'})
            job.update(source=source,board_id='rss' if source=='incruit' else 'alio',ncs_categories=ncs)
            if source=='incruit':job.update(is_open=None,employment_type=None)
            job_id=alio.upsert_job(db,job)
            db.execute('UPDATE job_posting SET rss_categories=? WHERE id=?',(json.dumps(categories or []),job_id))
            ids=[r[0] for r in db.execute("SELECT id FROM job_posting WHERE source='incruit'")]
            db.execute("INSERT OR REPLACE INTO job_source_snapshot VALUES('incruit','2026-01-01',?,?)",(len(ids),json.dumps(ids)))
        return job_id

    def test_direct_matches_sorted_before_category_only_prior_to_limit(self):
        self.seed("일반직 채용",ncs=["정보통신"],deadline="2098-01-01")
        direct=self.seed("백엔드 개발자",deadline="2099-01-01")
        self.assertEqual(search_jobs("IT",limit=1,source="alio")[0]['id'],direct)

    def test_same_matching_policy_for_both_sources(self):
        for source in ('alio','incruit'):
            self.seed("백엔드 개발자",source);self.seed("제품개발 경력",source)
            jobs=search_jobs('IT',source=source)
            self.assertEqual([j['title'] for j in jobs],["백엔드 개발자"])
            self.assertEqual(jobs[0]['search_match']['level'],'direct')

    def test_unified_limit_prefers_rss_direct_over_alio_related(self):
        self.seed("일반직 채용",ncs=["정보통신"]);direct=self.seed("백엔드 개발자","incruit")
        def invoke(args):
            jobs=search_jobs(args['keyword'],source=args['source'],limit=args['limit'])
            return {'ok':True,'data':{'jobs':jobs,'count':len(jobs)}}
        result=unified_search(invoke,{'keyword':'IT','limit':1})
        self.assertEqual(result['data']['jobs'][0]['id'],direct)
        self.assertEqual([r['source'] for r in result['data']['source_results']],['alio','incruit'])

    def test_equal_rank_preserves_source_interleaving_and_candidate_prefix(self):
        for source in ('alio','incruit'):
            for i in range(3):self.seed(f'백엔드 개발자 {i}',source)
        def invoke(args):
            jobs=search_jobs('IT',source=args['source'],limit=args['limit'])
            return {'ok':True,'data':{'jobs':jobs,'count':len(jobs)}}
        one=unified_search(invoke,{'limit':1})['data']['jobs']
        many=unified_search(invoke,{'limit':6})['data']['jobs']
        self.assertEqual(one[0]['id'],many[0]['id'])
        self.assertEqual([j['source'] for j in many],['alio','incruit']*3)

    def test_region_filter_and_snapshot_membership_preserved(self):
        self.seed('백엔드 개발자','incruit',region='서울')
        self.seed('백엔드 개발자','incruit',region='부산')
        self.assertEqual(len(search_jobs('IT',region='서울',source='incruit')),1)
        with closing(database.connect_db()) as db,db:db.execute("UPDATE job_source_snapshot SET item_count=0,job_ids='[]'")
        self.assertEqual(search_jobs('IT',source='incruit'),[])

    def test_rss_collection_unions_categories_on_duplicates(self):
        response=MagicMock();response.__enter__.return_value=response;response.status_code=200
        response.iter_content.side_effect=lambda size:[xml()]
        with patch.object(rss.requests,'get',return_value=response):jobs=rss.collect_jobs()
        self.assertEqual(len(jobs),1);self.assertEqual(jobs[0]['rss_categories'],['150','190'])

    def test_refresh_persists_feed_categories_without_overwriting_ncs(self):
        posting=rss.parse_feed(xml(),'2026-11-20T00:00:00+00:00')[0];posting['rss_categories']=['150']
        with patch.object(rss,'collect_jobs',return_value=[posting]):result=job_tools.search_jobs.invoke({'source':'incruit','keyword':'IT'})
        self.assertTrue(result['ok']);self.assertEqual(result['data']['jobs'][0]['rss_categories'],['150'])
        self.assertIsNone(result['data']['jobs'][0]['ncs_categories'])
        self.assertEqual(search_jobs('IT',source='incruit')[0]['rss_categories'],['150'])

    def test_old_database_migration_preserves_records_and_invalidates_cache(self):
        self.seed('일반직 채용','incruit')
        with closing(database.connect_db()) as db,db:
            db.execute('ALTER TABLE job_posting DROP COLUMN rss_categories')
            db.execute("INSERT INTO user_profile(desired_role) VALUES('보존할 프로필')")
            before=db.execute('SELECT id,title,required_qualifications FROM job_posting').fetchall()
        # ALIO reads work without a write/migration first.
        search_jobs('IT',source='alio')
        with closing(database.connect_db()) as db:
            rss.ensure_snapshot_table(db)
            self.assertEqual([tuple(x) for x in before],[tuple(x) for x in db.execute('SELECT id,title,required_qualifications FROM job_posting')])
            self.assertEqual(db.execute('SELECT count(*) FROM user_profile').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT rss_categories FROM job_posting').fetchone()[0],'[]')
            self.assertEqual(db.execute('SELECT fetched_at FROM job_source_snapshot').fetchone()[0],'')
            rss.ensure_snapshot_table(db)

    def test_unknown_deadline_and_qualifications_remain_unknown(self):
        job=self.seed('백엔드 개발자','incruit')
        with closing(database.connect_db()) as db,db:db.execute('UPDATE job_posting SET deadline=NULL WHERE id=?',(job,))
        result=search_jobs('IT',source='incruit')[0]
        self.assertIsNone(result['deadline']);self.assertIsNone(result['is_open'])
        detail=job_tools.get_job_detail.invoke({'job_id':job})
        self.assertIsNone(detail['data']['required_qualifications'])

    def test_chat_api_retains_matching_evidence_and_related_notice(self):
        from backend.app import career_agent
        from backend.app.chat_service import ChatService
        from test_execution_logging import FakeModel,FakeTool
        self.seed('일반직 채용',ncs=['정보통신'])
        def cached(args):
            jobs=search_jobs('IT',source='alio',limit=args['limit'])
            return {'ok':True,'data':{'jobs':jobs,'count':len(jobs)}}
        service=ChatService(model_factory=lambda:FakeModel([{'intents':['search_jobs'],'keyword':'IT','limit':1}]))
        with patch.dict(career_agent.TOOLS,search_jobs=FakeTool(cached)):
            result=service.invoke('alignment-chat','IT 공고',assess_fit=False)
        self.assertEqual(result['jobs'][0]['search_match']['level'],'related')
        self.assertIn('분야 분류만 일치',result['answer'])
        self.assertEqual(result['events'][-1]['event_type'],'run_finished')


if __name__=='__main__':unittest.main()
