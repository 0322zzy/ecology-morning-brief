#!/usr/bin/env python3
"""Regressions for evidence-backed chat/project interest refresh."""
import copy
import unittest
from datetime import datetime, timezone
from pathlib import Path

from brief_engine import load_config
from build_context_config import build_effective_config
from fetch_candidates import validate_context_refresh
from refresh_context import build_read_plan, compile_refresh
from search_planner import build_search_plan
from source_router import route_search_plan


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,11,9,1,tzinfo=timezone.utc)
        self.previous={"schema_version":1,"retrieval_mode":"extend","context_applied":True,
                       "focus_summary":"Published CNS and broad ecology","retrieval_queries":["thermal ecology"],
                       "primary_topics":{},"evidence":{"chat_basis":["persistent user preference"],"project_files":[]}}
        self.registry={"include_projects":[{"project_id":"research","label":"Ecology"}],
                       "exclude_project_ids":["personal"],"exclude_thread_ids":["brief"],
                       "exclude_title_terms":["旅游"],"discovery_terms":["ecology","衰老"],
                       "tracked_threads":[],"project_files":[],
                       "refresh":{"max_thread_reads":3,"max_context_modules":4,"max_active_interests":8,"interest_ttl_days":60,"exploratory_ttl_days":21}}
        self.source={"source_id":"chatgpt:a","kind":"chatgpt","thread_id":"a","project_id":"research",
                     "title":"建立蜥蜴衰老模型","updated_at":"2026-11-08T01:00:00Z","status":"ok",
                     "content":"我想研究蜥蜴年龄时钟与热压力的关系。", "uri":"https://chatgpt.com/c/a",
                     "messages":[{"role":"user","turn_id":"t1","text":"我想研究蜥蜴年龄时钟与热压力的关系。","timestamp":"2026-11-08T01:00:00Z"}]}
        self.bundle={"checked_at":self.now.isoformat(),"discovery":{"threads":{"status":"ok"}},"sources":[self.source]}
        self.decisions={"upsert_interests":[{"id":"aging","label":"蜥蜴年龄时钟","summary":"关注年龄与热压力",
                           "strength":"explicit","keywords":["epigenetic clock","thermal stress"],
                           "queries":{"direct":"epigenetic clock reptile","method":"biological age prediction"},
                           "evidence":[{"source_id":"chatgpt:a","quote":"研究蜥蜴年龄时钟与热压力的关系"}]}]}

    def compile(self,previous=None,state=None,bundle=None,decisions=None):
        return compile_refresh(previous or self.previous,state or {},bundle or self.bundle,
                               self.decisions if decisions is None else decisions,self.registry,"2026-11-09")

    def test_web_user_evidence_enters_actual_search_plan(self):
        profile,state,audit=self.compile()
        config=load_config(Path(__file__).resolve().parents[1]/"config/interests.yaml")
        effective=build_effective_config(config,profile)
        plan=build_search_plan(effective,profile,mode="daily",report_date="2026-11-09")
        route=route_search_plan(plan,effective)
        self.assertIn("epigenetic clock reptile",[q["query"] for q in plan["queries"]])
        self.assertTrue(set(config["search"]["modules"]).issubset(set(plan["modules"])))
        self.assertTrue(route["sources"]["journal_watch"]["active_core"])
        self.assertEqual(audit["refresh"]["web_chats_read"],1)
        self.assertEqual(config["delivery"],effective["delivery"])

    def test_assistant_recommendation_cannot_be_user_interest_evidence(self):
        bundle=copy.deepcopy(self.bundle)
        bundle["sources"][0]["messages"][0]["role"]="assistant"
        with self.assertRaises(ValueError):self.compile(bundle=bundle)

    def test_invented_quote_is_rejected(self):
        decision=copy.deepcopy(self.decisions)
        decision["upsert_interests"][0]["evidence"][0]["quote"]="用户没有说过的研究兴趣"
        with self.assertRaises(ValueError):self.compile(decisions=decision)

    def test_unchanged_repeated_source_does_not_claim_new_interest(self):
        profile,state,audit=self.compile()
        again,newstate,second=self.compile(previous=profile,state=state)
        self.assertEqual(second["interest_changes"],[])
        self.assertEqual(second["refresh"]["status"],"no_change")
        self.assertEqual(state["interests"]["aging"]["last_supported_at"],newstate["interests"]["aging"]["last_supported_at"])
        self.assertEqual(second["before_queries"],second["after_queries"])

    def test_failed_discovery_preserves_previous_interests(self):
        profile,state,_=self.compile()
        bundle={"checked_at":self.now.isoformat(),"discovery":{"threads":{"status":"failed"}},"sources":[]}
        again,_,audit=self.compile(previous=profile,state=state,bundle=bundle,decisions={})
        self.assertIn("context_aging",again["primary_topics"])
        self.assertEqual(audit["refresh"]["status"],"degraded")
        self.assertEqual(audit["interest_changes"],[])

    def test_old_exploratory_interest_and_keywords_remain_without_new_discussion(self):
        d=copy.deepcopy(self.decisions);d["upsert_interests"][0]["strength"]="exploratory"
        profile,state,_=self.compile(decisions=d)
        later={"checked_at":"2026-12-01T01:00:00Z","sources":[],"discovery":{}}
        again,_,audit=self.compile(previous=profile,state=state,bundle=later,decisions={})
        self.assertIn("context_aging",again["primary_topics"])
        self.assertIn("thermal ecology",again["retrieval_queries"])
        self.assertIn("epigenetic clock",again["keyword_bank"])
        self.assertEqual(audit["interest_changes"],[])

    def test_topic_optimization_adds_terms_and_retains_old_query_variants(self):
        profile,state,_=self.compile()
        update=copy.deepcopy(self.decisions)
        update["upsert_interests"][0]["keywords"]=["biological age"]
        update["upsert_interests"][0]["queries"]={"direct":"reptile biological age prediction"}
        again,newstate,audit=self.compile(previous=profile,state=state,decisions=update)
        self.assertTrue(set(profile["primary_topics"]["context_aging"]["keywords"]).issubset(again["primary_topics"]["context_aging"]["keywords"]))
        self.assertIn("biological age",again["primary_topics"]["context_aging"]["keywords"])
        self.assertIn("epigenetic clock reptile",again["retrieval_queries"])
        self.assertIn("reptile biological age prediction",again["retrieval_queries"])
        self.assertIn("epigenetic clock reptile",newstate["interests"]["aging"]["query_variants"]["direct"])
        self.assertEqual(audit["keyword_changes"]["removed"],[])

    def test_daily_rotation_does_not_replace_retained_queries_or_keywords(self):
        proposal=self.decisions["upsert_interests"][0]
        decisions={"upsert_interests":[]}
        for index in range(10):
            item=copy.deepcopy(proposal);item["id"]="theme_"+str(index)
            item["keywords"]=["retained keyword "+str(index)]
            item["queries"]={"direct":"retained query "+str(index)}
            decisions["upsert_interests"].append(item)
        profile,state,_=self.compile(decisions=decisions)
        self.assertEqual(len(profile["primary_topics"]),10)
        self.assertEqual(len(profile["context_modules"]),4)
        bundle={"checked_at":"2026-11-10T01:00:00Z","discovery":{},"sources":[]}
        again,_,audit=compile_refresh(profile,state,bundle,{},self.registry,"2026-11-10")
        self.assertTrue(set(profile["retrieval_queries"]).issubset(again["retrieval_queries"]))
        self.assertEqual(set(profile["keyword_bank"]),set(again["keyword_bank"]))
        self.assertEqual(set(profile["primary_topics"]),set(again["primary_topics"]))
        self.assertEqual(audit["keyword_changes"]["removed"],[])

    def test_deactivation_requires_explicit_user_request(self):
        profile,state,_=self.compile()
        decisions={"deactivate_interests":[{"id":"aging","reason":"no recent discussion","evidence":self.decisions["upsert_interests"][0]["evidence"]}]}
        with self.assertRaises(ValueError):
            self.compile(previous=profile,state=state,decisions=decisions)

    def test_explicit_user_deactivation_is_recorded_and_preserves_other_queries(self):
        profile,state,_=self.compile()
        bundle=copy.deepcopy(self.bundle)
        text="请停止追踪蜥蜴年龄时钟这个研究兴趣。"
        bundle["sources"][0]["messages"].append({"role":"user","turn_id":"t2","text":text,"timestamp":"2026-11-09T00:00:00Z"})
        decisions={"deactivate_interests":[{"id":"aging","user_requested":True,"reason":"用户明确停止追踪",
                    "evidence":[{"source_id":"chatgpt:a","quote":text}]}]}
        again,_,audit=self.compile(previous=profile,state=state,bundle=bundle,decisions=decisions)
        self.assertNotIn("context_aging",again["primary_topics"])
        self.assertIn("thermal ecology",again["retrieval_queries"])
        self.assertEqual(audit["interest_changes"][0]["action"],"deactivated")

    def test_personal_project_is_excluded_even_with_scientific_title(self):
        bundle=copy.deepcopy(self.bundle);bundle["sources"][0]["project_id"]="personal"
        with self.assertRaises(ValueError):self.compile(bundle=bundle)

    def test_zero_change_still_has_fresh_audit(self):
        config={"context_refresh":{"enabled":True,"max_age_hours":36}}
        p={"_context_refresh":{"checked_at":self.now.isoformat(),"status":"no_change"}}
        self.assertEqual(validate_context_refresh(config,p,self.now)["status"],"no_change")
        p["_context_refresh"]["status"]="degraded"
        self.assertEqual(validate_context_refresh(config,p,self.now)["status"],"degraded")

    def test_missing_or_stale_refresh_cannot_silently_reuse_profile(self):
        config={"context_refresh":{"enabled":True,"max_age_hours":36}}
        with self.assertRaises(ValueError):validate_context_refresh(config,{},self.now)
        p={"_context_refresh":{"checked_at":"2026-11-02T01:00:00Z","status":"updated"}}
        with self.assertRaises(ValueError):validate_context_refresh(config,p,self.now)

    def test_yesterdays_fresh_audit_does_not_replace_todays_refresh(self):
        config={"context_refresh":{"enabled":True,"max_age_hours":36}}
        profile={"_context_refresh":{"checked_at":"2026-11-08T01:00:00Z","status":"no_change","report_date":"2026-11-08"}}
        with self.assertRaises(ValueError):
            validate_context_refresh(config,profile,self.now,report_date="2026-11-09")

    def test_incremental_plan_accepts_equivalent_timestamp_formats(self):
        profile,state,_=self.compile()
        inventory={"threads":[{"id":"a","kind":"chatgpt","title":self.source["title"],"projectId":"research",
                              "updatedAt":datetime(2026,11,8,1,tzinfo=timezone.utc).timestamp()}]}
        plan=build_read_plan(inventory,self.registry,state,self.now)
        self.assertEqual(plan["thread_reads"],[])
        self.assertEqual(plan["unchanged_sources"],["chatgpt:a"])

    def test_tracked_older_web_chat_remains_recheckable(self):
        registry=copy.deepcopy(self.registry)
        registry["tracked_threads"]=[{"thread_id":"old","kind":"chatgpt","title":"旧研究讨论"}]
        plan=build_read_plan({"threads":[]},registry,{},self.now)
        self.assertEqual(plan["thread_reads"][0]["thread_id"],"old")
        self.assertFalse(plan["coverage"]["all_history_scanned"])


if __name__=="__main__":unittest.main(verbosity=2)
