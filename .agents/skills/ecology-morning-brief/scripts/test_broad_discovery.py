#!/usr/bin/env python3
"""Behavioral regressions for broad discovery and publication mix."""
import copy
import unittest
from datetime import datetime, timezone
from pathlib import Path
from brief_engine import Article, APIClient, collect_europepmc, load_config, normalize_article, journal_group
from check_selection import check_selection
from evaluate_coverage import build_coverage_report
from fetch_candidates import _rank, _shortlist
from journal_watch import collect_journal_watch
from search_planner import build_search_plan
from source_router import apply_route_to_config, route_search_plan

class BroadDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.config=load_config(Path(__file__).resolve().parents[1]/'config/interests.yaml')
        self.start=datetime(2026,10,26,tzinfo=timezone.utc)
        self.end=datetime(2026,11,2,tzinfo=timezone.utc)

    def paper(self,index,**kwargs):
        return normalize_article(Article(title=f'Distinct scientific contribution {index}',doi=f'10.9999/{index}',journal='Nature',publication_type='journal-article',abstract='Author-supported scientific abstract.',**kwargs))

    def test_index_specific_journal_titles_keep_family_identity(self):
        self.assertEqual(journal_group('Science (New York, N.Y.)', self.config), 'cns')
        self.assertEqual(journal_group('Nature Ecology and Evolution', self.config), 'cns')
        self.assertEqual(journal_group('Not Nature', self.config), '')

    def test_ppr_source_is_detected_even_without_pubtype(self):
        class Client:
            def get_json(self,*args,**kwargs):
                return {'resultList':{'result':[{'title':'A preprint','source':'PPR','id':'1'}]},'hitCount':1}
        records,_,_=collect_europepmc(Client(),self.config,self.start,self.end)
        self.assertTrue(records[0].is_preprint)
        self.assertEqual(records[0].publication_status,'preprint')

    def test_repository_venue_cannot_masquerade_as_published(self):
        for venue in ('bioRxiv','Research Square','arXiv'):
            a=normalize_article(Article(title='Article',journal=venue,publication_type='Journal Article'))
            self.assertEqual(a.publication_status,'preprint')

    def test_shards_continue_after_failure_and_preserve_zero_results(self):
        calls=[]
        class Client:
            def get_json(self,source,url,params):
                calls.append(params)
                if 'failed-topic' in params['query']:raise RuntimeError('source query failed')
                return {'resultList':{'result':[]},'hitCount':0}
        config=copy.deepcopy(self.config);config['europepmc_queries']=['failed-topic','another-topic']
        records,partial,error=collect_europepmc(Client(),config,self.start,self.end)
        self.assertEqual(len(calls),2);self.assertTrue(partial);self.assertIn('failed',error)
        self.assertTrue(all('NOT SRC:PPR' in p['query'] for p in calls))
        self.assertTrue(all('sort_date:y' in p['query'] for p in calls))

    def test_shards_receive_fair_capacity(self):
        calls=[]
        class Client:
            def get_json(self,source,url,params):
                calls.append(params['pageSize']);return {'resultList':{'result':[]},'hitCount':0}
        c=copy.deepcopy(self.config);c['sources']['europepmc']['max_articles']=10;c['europepmc_queries']=['a','b']
        collect_europepmc(Client(),c,self.start,self.end)
        self.assertEqual(calls,[5,5])

    def test_journal_browsing_is_independent_and_survives_one_failure(self):
        calls=[]
        class Client:
            def get_json(self,source,url,params):
                calls.append((url,params))
                if source=='europepmc': return {'resultList':{'result':[]},'hitCount':0}
                if '/0036-8075/' in url:raise RuntimeError('unavailable')
                return {'message':{'items':[{'DOI':'10.9999/novel','title':['A new conceptual biological mechanism'],'container-title':['Nature'],'type':'journal-article','published':{'date-parts':[[2026,10,30]]}}]}}
        c=copy.deepcopy(self.config);c['journal_watch']['journals']=c['journal_watch']['journals'][:2]
        records,partial,_=collect_journal_watch(Client(),c,self.start,self.end)
        self.assertEqual(len(records),1);self.assertTrue(partial)
        self.assertIn('journal_watch',records[0].discovery_methods)
        self.assertNotIn('query.bibliographic', calls[0][1])
        self.assertIn('ISSN:0028-0836',calls[-1][1]['query'])

    def test_wrong_venue_from_issn_endpoint_is_excluded(self):
        class Client:
            def get_json(self,source,url,params):
                if source=='europepmc':return {'resultList':{'result':[]},'hitCount':0}
                return {'message':{'items':[{'DOI':'10.9999/bad','title':['Unrelated venue'],'container-title':['Wrong Journal'],'type':'journal-article'}]}}
        c=copy.deepcopy(self.config);c['journal_watch']['journals']=c['journal_watch']['journals'][:1]
        records,partial,error=collect_journal_watch(Client(),c,self.start,self.end)
        self.assertEqual(records,[]);self.assertTrue(partial);self.assertIn('unexpected venue',error)

    def test_low_topic_score_journal_paper_reaches_editorial_review(self):
        article=self.paper(1,discovery_methods=['journal_watch'])
        candidates=_shortlist(_rank([article],self.config),self.config,10,90)
        self.assertEqual(len(candidates),1)

    def test_method_without_ecology_anchor_reaches_review(self):
        a=normalize_article(Article(title='Simulation-based inference with calibrated uncertainty',abstract='A general inference algorithm for latent dynamical models.',journal='Statistical Science',publication_type='journal-article',doi='10.9999/method',query_ids=['q-method']))
        records=_shortlist(_rank([a],self.config),self.config,10,12)
        self.assertTrue(records)

    def test_published_mix_and_preprint_cap(self):
        records=[self.paper(i).serializable() for i in range(40)]
        for i in range(3): records[i].update(is_preprint=True,journal='bioRxiv')
        self.assertTrue(check_selection(records,self.config)['ok'])
        records[3].update(is_preprint=True,journal='bioRxiv')
        self.assertFalse(check_selection(records,self.config)['ok'])

    def test_duplicate_and_unknown_versions_do_not_pass(self):
        records=[self.paper(i).serializable() for i in range(40)]
        records[1]=records[0].copy()
        self.assertFalse(check_selection(records,self.config)['ok'])
        for row in records:row.update(journal='',publication_type='article',sources=[])
        result=check_selection(records,self.config)
        self.assertEqual(result['counts']['published'],0)

    def test_focus_disables_independent_journal_scan(self):
        profile={'context_applied':True,'retrieval_mode':'focus','retrieval_queries':['thermal tolerance']}
        plan=build_search_plan(self.config,profile,mode='topic-deep',report_date='2026-11-02')
        route=route_search_plan(plan,self.config)
        self.assertFalse(route['sources']['journal_watch']['active_core'])
        self.assertEqual(plan['modules'],['context_focus'])

    def test_daily_routes_broad_modules_and_published_shards(self):
        plan=build_search_plan(self.config,mode='daily',report_date='2026-11-02')
        route=route_search_plan(plan,self.config)
        routed=apply_route_to_config(self.config,plan,route)
        self.assertTrue(route['sources']['journal_watch']['active_core'])
        self.assertIn('frontier_methods',plan['modules'])
        self.assertGreater(len(routed['europepmc_queries']),1)
        self.assertIn('"causal inference"',routed['europepmc_queries'])

    def test_outage_prevents_high_saturation(self):
        c=copy.deepcopy(self.config);c['search']['coverage'].update(minimum_relevant_for_high=1,low_gain_streak=1)
        payload={'sources':{'openalex':{'status':'failed','count':0}},'search_plan':{'queries':[{'query_id':'q1'}]},'retrieval_audit':{'query_attempts':[{'query_ids':['q1'],'status':'ok'}],'records':[{'title':'A','relevant':True,'sources':['other'],'query_ids':[]}]}}
        result=build_coverage_report(payload,c)
        self.assertNotEqual(result['operational_search_saturation']['level'],'high')
        self.assertTrue(result['queries'][0]['successful'])
        self.assertEqual(result['queries'][0]['searched'],0)

if __name__=='__main__':unittest.main(verbosity=2)
