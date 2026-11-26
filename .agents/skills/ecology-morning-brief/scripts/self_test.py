#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from brief_engine import (
    Article,
    HistoryDB,
    SourceUnavailable,
    collect_google_scholar,
    collect_openalex,
    collect_semantic_scholar,
    collect_webofscience,
    classify_relevance,
    deduplicate,
    load_config,
    normalize_article,
    normalize_doi,
    score_article,
)
from build_context_config import build_effective_config
from citation_network import expand_openalex
from evaluate_coverage import build_coverage_report
from fetch_candidates import COLLECTORS, _run_sources
from search_planner import build_search_plan
from source_router import apply_route_to_config, route_search_plan
from verify_metadata import evaluate_match


SKILL_DIR = Path(__file__).resolve().parents[1]


class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(SKILL_DIR / "config" / "interests.yaml")

    def test_config_is_json_compatible_yaml(self) -> None:
        self.assertIn("primary_topics", self.config)
        self.assertGreaterEqual(self.config["lookback_hours"], 24)
        self.assertGreaterEqual(len(self.config["primary_topics"]), 15)
        self.assertTrue(self.config["sources"]["openalex"]["enabled"])
        self.assertTrue(self.config["sources"]["semantic_scholar"]["enabled"])
        self.assertTrue(self.config["sources"]["webofscience"]["enabled"])
        self.assertFalse(self.config["sources"]["google_scholar"]["enabled"])

    def test_doi_and_title_deduplication(self) -> None:
        records = deduplicate(
            [
                Article(title="Thermal Ecology: One Test", doi="https://doi.org/10.1/ABC", sources=["crossref"]),
                Article(title="thermal ecology — one test", doi="10.1/abc", sources=["europepmc"]),
            ],
            0.94,
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].doi, "10.1/abc")
        self.assertEqual(records[0].sources, ["crossref", "europepmc"])

    def test_normalized_title_duplicate_without_doi_is_merged(self) -> None:
        records = deduplicate(
            [
                Article(title="Thermal limits across life-stages", sources=["biorxiv"]),
                Article(title="thermal limits across life stages!", sources=["europepmc"]),
            ],
            0.94,
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].sources, ["biorxiv", "europepmc"])

    def test_placeholder_published_doi_does_not_merge_preprints(self) -> None:
        self.assertEqual(normalize_doi("NA"), "")
        records = deduplicate(
            [
                normalize_article(
                    Article(title="First unrelated preprint", doi="10.1/first", related_dois=["NA"])
                ),
                normalize_article(
                    Article(title="Second unrelated preprint", doi="10.1/second", related_dois=["N/A"])
                ),
            ],
            0.94,
        )
        self.assertEqual(len(records), 2)

    def test_relevant_combination_scores_above_biomedical_false_positive(self) -> None:
        relevant = normalize_article(
            Article(
                title="Thermal tolerance of lizard embryos under climate change",
                abstract="A reptile study integrating CTmax and species distribution models.",
            )
        )
        biomedical = normalize_article(
            Article(
                title="Embryonic arrest after IVF",
                abstract="A clinical study of patients and maternal insulin signalling.",
            )
        )
        score_article(relevant, self.config)
        score_article(biomedical, self.config)
        self.assertGreater(relevant.rule_score, biomedical.rule_score)
        self.assertGreaterEqual(relevant.rule_score, self.config["candidate_score_threshold"])
        self.assertLess(biomedical.rule_score, self.config["candidate_score_threshold"])

    def test_history_is_marked_only_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            article = normalize_article(Article(title="A new ecology paper", doi="10.1/test"))
            database = HistoryDB(Path(directory))
            database.upsert_seen(article)
            self.assertFalse(article.previously_reported)
            database.mark_reported(article.doi, article.title, "2026-09-18")
            second = normalize_article(Article(title=article.title, doi=article.doi))
            database.upsert_seen(second)
            self.assertTrue(second.previously_reported)
            database.close()

    def test_architecture_thermal_performance_is_demoted(self) -> None:
        article = normalize_article(
            Article(
                title="Thermal performance in residential architecture",
                abstract="A study of daylight and building performance.",
            )
        )
        score_article(article, self.config)
        self.assertLess(article.rule_score, self.config["candidate_score_threshold"])

    def test_context_profile_extends_queries_and_topics_without_mutating_base(self) -> None:
        profile = {
            "schema_version": 1,
            "retrieval_mode": "extend",
            "focus_summary": "Project focus on nest thermal ecology.",
            "europepmc_terms": ["nest thermal ecology", "Takydromus"],
            "retrieval_queries": ["lizard nest thermal ecology"],
            "primary_topics": {
                "nest_thermal_ecology": {
                    "label": "Nest thermal ecology",
                    "weight": 1.3,
                    "category": "Life-stage thermal vulnerability",
                    "keywords": ["nest thermal ecology", "nest temperature"],
                }
            },
            "evidence": {"chat_basis": ["lizard embryo project"]},
        }
        effective = build_effective_config(self.config, profile)
        self.assertIn("lizard nest thermal ecology", effective["retrieval_queries"])
        self.assertIn("nest_thermal_ecology", effective["primary_topics"])
        self.assertNotIn("nest_thermal_ecology", self.config["primary_topics"])
        self.assertEqual(
            effective["primary_topics"]["nest_thermal_ecology"]["category"],
            "Life-stage thermal vulnerability",
        )
        self.assertEqual(effective["retrieval_queries"][0], "lizard nest thermal ecology")
        self.assertEqual(
            effective["google_scholar_queries"][0], "lizard nest thermal ecology"
        )
        self.assertEqual(
            effective["openalex_queries"][0], "lizard nest thermal ecology"
        )
        self.assertEqual(
            effective["semantic_scholar_queries"][0], "lizard nest thermal ecology"
        )
        self.assertEqual(
            effective["wos_queries"][0], "TS=(lizard nest thermal ecology)"
        )
        self.assertTrue(effective["_context"]["applied"])

    def test_context_focus_mode_replaces_only_retrieval_queries(self) -> None:
        profile = {
            "schema_version": 1,
            "retrieval_mode": "focus",
            "retrieval_queries": ["Takydromus embryo thermal tolerance"],
            "europepmc_terms": ["Takydromus", "embryo thermal tolerance"],
            "evidence": {},
        }
        effective = build_effective_config(self.config, profile)
        self.assertEqual(
            effective["retrieval_queries"], ["Takydromus embryo thermal tolerance"]
        )
        self.assertEqual(effective["sources"], self.config["sources"])
        self.assertEqual(effective["scoring"], self.config["scoring"])
        self.assertEqual(
            effective["google_scholar_queries"],
            ["Takydromus embryo thermal tolerance"],
        )
        self.assertEqual(
            effective["openalex_queries"],
            ["Takydromus embryo thermal tolerance"],
        )
        self.assertEqual(
            effective["semantic_scholar_queries"],
            ["Takydromus embryo thermal tolerance"],
        )
        self.assertEqual(
            effective["wos_queries"], ["TS=(Takydromus embryo thermal tolerance)"]
        )

    def test_europepmc_routing_preserves_baseline_and_adds_only_context_focus(self) -> None:
        profile = {
            "schema_version": 1,
            "retrieval_mode": "extend",
            "retrieval_queries": ["Takydromus embryo thermal tolerance"],
            "europepmc_terms": ["Takydromus", "embryo thermal tolerance"],
            "evidence": {},
        }
        effective = build_effective_config(self.config, profile)
        plan = build_search_plan(
            effective, profile, mode="daily", report_date="2026-09-29", lookback=48
        )
        route = route_search_plan(plan, effective)
        routed = apply_route_to_config(effective, plan, route)
        self.assertIn('"thermal ecology"', routed["europepmc_query"])
        self.assertIn('"Takydromus embryo thermal tolerance"', routed["europepmc_query"])
        self.assertNotIn('"population persistence"', routed["europepmc_query"])

    def test_time_dependent_thermal_tolerance_is_prioritized(self) -> None:
        article = normalize_article(
            Article(
                title="Thermal death time reveals dynamic heat tolerance in an ectotherm",
                abstract=(
                    "Exposure duration and thermal injury accumulation predict survival "
                    "across life stages under repeated heat stress."
                ),
            )
        )
        score_article(article, self.config)
        self.assertGreaterEqual(article.rule_score, self.config["candidate_score_threshold"])
        self.assertIn("time_dependent_tolerance", article.matched_topics)
        self.assertEqual(article.category, "Thermal & physiological ecology")

    def test_stage_structured_climate_model_is_prioritized(self) -> None:
        article = normalize_article(
            Article(
                title="Stage-structured population model of climate vulnerability",
                abstract=(
                    "A Lefkovitch matrix links embryo, juvenile and adult vital rates "
                    "to population viability under warming."
                ),
            )
        )
        score_article(article, self.config)
        self.assertGreaterEqual(article.rule_score, self.config["candidate_score_threshold"])
        self.assertIn("stage_structured_models", article.matched_topics)
        self.assertEqual(article.category, "Population ecology & demography")

    def test_engineering_digital_twin_is_demoted(self) -> None:
        article = normalize_article(
            Article(
                title="Digital twin optimization for a smart factory power grid",
                abstract="An industrial manufacturing energy system and supply chain model.",
            )
        )
        score_article(article, self.config)
        self.assertLess(article.rule_score, self.config["candidate_score_threshold"])

    def test_authenticated_sources_require_environment_credentials(self) -> None:
        start = datetime(2026, 9, 19, tzinfo=timezone.utc)
        end = datetime(2026, 9, 21, tzinfo=timezone.utc)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SourceUnavailable):
                collect_webofscience(None, self.config, start, end)  # type: ignore[arg-type]
            with self.assertRaises(SourceUnavailable):
                collect_google_scholar(None, self.config, start, end)  # type: ignore[arg-type]

    def test_webofscience_record_is_normalized_without_claiming_an_abstract(self) -> None:
        class FakeClient:
            def get_json(self, source, url, params=None, headers=None):
                self.source = source
                self.url = url
                self.params = params
                self.headers = headers
                return {
                    "metadata": {"total": 1, "page": 1, "limit": 50},
                    "hits": [
                        {
                            "uid": "WOS:123",
                            "title": "Ontogenetic thermal tolerance in a lizard",
                            "types": ["Article"],
                            "source": {
                                "sourceTitle": "Functional Ecology",
                                "publishYear": 2026,
                                "publishMonth": "SEP",
                            },
                            "names": {
                                "authors": [{"displayName": "A Researcher"}]
                            },
                            "links": {"record": "https://www.webofscience.com/wos/123"},
                            "identifiers": {"doi": "10.1/WOS-TEST"},
                            "keywords": {"authorKeywords": ["thermal tolerance"]},
                        }
                    ],
                }

        client = FakeClient()
        start = datetime(2026, 9, 19, tzinfo=timezone.utc)
        end = datetime(2026, 9, 21, tzinfo=timezone.utc)
        config = json.loads(json.dumps(self.config))
        config["wos_queries"] = ["TS=(thermal tolerance)"]
        with patch.dict(os.environ, {"WOS_API_KEY": "test-key"}, clear=True):
            articles, partial, error = collect_webofscience(client, config, start, end)
        self.assertFalse(partial)
        self.assertEqual(error, "")
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0].doi, "10.1/wos-test")
        self.assertEqual(articles[0].abstract, "")
        self.assertEqual(articles[0].published_date, "2026-09")
        self.assertEqual(client.headers, {"X-ApiKey": "test-key"})
        self.assertNotIn("test-key", str(client.params))

    def test_openalex_record_reconstructs_abstract_and_uses_optional_key(self) -> None:
        class FakeClient:
            def get_json(self, source, url, params=None, headers=None):
                self.source = source
                self.params = params
                self.headers = headers
                return {
                    "results": [
                        {
                            "id": "https://openalex.org/W123",
                            "doi": "https://doi.org/10.1/OPENALEX-TEST",
                            "title": "Ontogenetic thermal tolerance in a lizard",
                            "authorships": [
                                {"author": {"display_name": "A Researcher"}}
                            ],
                            "abstract_inverted_index": {
                                "Thermal": [0],
                                "tolerance": [1],
                                "varies": [2],
                                "ontogenetically.": [3],
                            },
                            "publication_date": "2026-09-20",
                            "primary_location": {
                                "landing_page_url": "https://example.org/paper",
                                "source": {"display_name": "Functional Ecology"},
                            },
                            "type": "article",
                            "topics": [{"display_name": "Thermal Ecology"}],
                            "keywords": [{"display_name": "Thermal tolerance"}],
                        }
                    ]
                }

        client = FakeClient()
        start = datetime(2026, 9, 19, tzinfo=timezone.utc)
        end = datetime(2026, 9, 21, tzinfo=timezone.utc)
        config = json.loads(json.dumps(self.config))
        config["openalex_queries"] = ["thermal tolerance"]
        with patch.dict(os.environ, {"OPENALEX_API_KEY": "test-key"}, clear=True):
            articles, partial, error = collect_openalex(client, config, start, end)
        self.assertFalse(partial)
        self.assertEqual(error, "")
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0].doi, "10.1/openalex-test")
        self.assertEqual(
            articles[0].abstract, "Thermal tolerance varies ontogenetically."
        )
        self.assertEqual(articles[0].journal, "Functional Ecology")
        self.assertEqual(client.headers, {"Authorization": "Bearer test-key"})
        self.assertNotIn("test-key", str(client.params))

    def test_semantic_scholar_record_uses_abstract_and_optional_key(self) -> None:
        class FakeClient:
            def get_json(self, source, url, params=None, headers=None):
                self.source = source
                self.params = params
                self.headers = headers
                return {
                    "total": 1,
                    "offset": 0,
                    "data": [
                        {
                            "paperId": "s2-test",
                            "externalIds": {"DOI": "10.1/S2-TEST"},
                            "url": "https://www.semanticscholar.org/paper/s2-test",
                            "title": "Thermal tolerance across life stages",
                            "abstract": "Embryos and adults differ in heat tolerance.",
                            "authors": [{"name": "A Researcher"}],
                            "venue": "Journal of Thermal Biology",
                            "publicationDate": "2026-09-20",
                            "publicationTypes": ["JournalArticle"],
                            "fieldsOfStudy": ["Biology"],
                        }
                    ],
                }

        client = FakeClient()
        start = datetime(2026, 9, 19, tzinfo=timezone.utc)
        end = datetime(2026, 9, 21, tzinfo=timezone.utc)
        config = json.loads(json.dumps(self.config))
        config["semantic_scholar_queries"] = ["thermal-tolerance life stage"]
        config["sources"]["semantic_scholar"]["request_interval_seconds"] = 0
        with patch.dict(
            os.environ, {"SEMANTIC_SCHOLAR_API_KEY": "test-key"}, clear=True
        ):
            articles, partial, error = collect_semantic_scholar(
                client, config, start, end
            )
        self.assertFalse(partial)
        self.assertEqual(error, "")
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0].doi, "10.1/s2-test")
        self.assertEqual(
            articles[0].abstract, "Embryos and adults differ in heat tolerance."
        )
        self.assertEqual(articles[0].sources, ["semantic_scholar"])
        self.assertEqual(client.headers, {"x-api-key": "test-key"})
        self.assertEqual(client.params["query"], "thermal tolerance life stage")

    def test_google_scholar_snippet_is_not_treated_as_an_abstract(self) -> None:
        class FakeClient:
            def get_json(self, source, url, params=None, headers=None):
                return {
                    "search_metadata": {"status": "Success"},
                    "organic_results": [
                        {
                            "title": "Thermal tolerance across life stages",
                            "link": "https://doi.org/10.1/SCHOLAR-TEST",
                            "snippet": "This search-result snippet is not an abstract.",
                            "publication_info": {
                                "summary": "A Researcher - Ecology Letters, 2026 - example.org",
                                "authors": [{"name": "A Researcher"}],
                            },
                        }
                    ],
                }

        start = datetime(2026, 9, 19, tzinfo=timezone.utc)
        end = datetime(2026, 9, 21, tzinfo=timezone.utc)
        config = json.loads(json.dumps(self.config))
        config["google_scholar_queries"] = ["thermal tolerance life stage"]
        with patch.dict(os.environ, {"SERPAPI_KEY": "test-key"}, clear=True):
            articles, partial, error = collect_google_scholar(
                FakeClient(), config, start, end
            )
        self.assertFalse(partial)
        self.assertEqual(error, "")
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0].doi, "10.1/scholar-test")
        self.assertEqual(articles[0].abstract, "")
        self.assertEqual(articles[0].journal, "Ecology Letters")
        self.assertEqual(articles[0].sources, ["google_scholar"])

    def test_v2_query_planner_is_orthogonal_and_bounded(self) -> None:
        plan = build_search_plan(self.config, mode="daily", report_date="2026-09-29")
        self.assertGreater(len(plan["queries"]), 1)
        self.assertLessEqual(
            len(plan["queries"]),
            plan["limits"]["max_modules"] * plan["limits"]["max_queries_per_module"] + 3,
        )
        query_types = {query["query_type"] for query in plan["queries"]}
        self.assertIn("direct", query_types)
        self.assertIn("method", query_types)
        self.assertTrue(all(query["terms"] for query in plan["queries"]))

    def test_legacy_configuration_without_search_modules_still_plans(self) -> None:
        legacy = json.loads(json.dumps(self.config))
        legacy.pop("search")
        plan = build_search_plan(legacy, mode="daily", report_date="2026-09-29")
        self.assertTrue(plan["queries"])
        self.assertTrue(all(module.startswith("legacy_") for module in plan["modules"]))
        self.assertLessEqual(len(plan["modules"]), 5)

    def test_explicit_focus_replaces_default_modules(self) -> None:
        profile = {
            "schema_version": 1,
            "retrieval_mode": "focus",
            "context_applied": True,
            "focus_summary": "Integrating physiology into species distribution models",
            "retrieval_queries": ["physiological species distribution model"],
            "primary_topics": {
                "physiological_sdm": {
                    "keywords": ["physiological species distribution model", "mechanistic niche model"]
                }
            },
        }
        plan = build_search_plan(
            self.config, profile, mode="topic-deep", report_date="2026-09-29"
        )
        self.assertEqual(plan["modules"], ["context_focus"])
        self.assertEqual(plan["queries"][0]["query"], "physiological species distribution model")
        self.assertEqual(
            {query["query_type"] for query in plan["queries"]},
            {"direct", "mechanism", "method", "application", "review", "frontier_transfer"},
        )

    def test_topic_deep_adds_review_discovery_and_citation_seeds(self) -> None:
        profile = {
            "schema_version": 1,
            "retrieval_mode": "focus",
            "context_applied": True,
            "focus_summary": "Integrating physiology into species distribution models",
            "retrieval_queries": ["physiological species distribution model"],
            "primary_topics": {
                "physiology_sdm": {
                    "keywords": ["physiological species distribution model", "mechanistic niche model"]
                }
            },
        }
        plan = build_search_plan(
            self.config, profile, mode="topic-deep", report_date="2026-09-29"
        )
        review_queries = [query for query in plan["queries"] if query["query_type"] == "review"]
        self.assertTrue(review_queries)
        self.assertTrue(all(query["citation_expansion_allowed"] for query in review_queries))
        self.assertEqual(plan["limits"]["max_hops"], 1)

    def test_cross_disciplinary_forecasting_is_classed_as_method_transfer(self) -> None:
        article = normalize_article(
            Article(
                title="Bayesian ecological forecasting of marine population dynamics",
                abstract="A state-space model supports biodiversity forecasting and conservation.",
                query_ids=["q01-03-forecasting_methods-method"],
            )
        )
        classify_relevance(score_article(article, self.config), self.config)
        self.assertEqual(article.relevance_class, "method_transfer")
        self.assertGreaterEqual(article.rule_score, self.config["candidate_score_threshold"])

    def test_source_router_separates_core_and_boosters(self) -> None:
        plan = build_search_plan(self.config, mode="weekly-deep", report_date="2026-09-29")
        route = route_search_plan(plan, self.config)
        self.assertTrue(route["sources"]["crossref"]["active_core"])
        self.assertTrue(route["sources"]["openalex"]["active_booster"])
        self.assertTrue(route["sources"]["webofscience"]["active_booster"])
        effective = apply_route_to_config(self.config, plan, route)
        self.assertTrue(effective["openalex_queries"])
        self.assertIn("query_lookup", effective["_search_plan"])

    def test_preprint_and_published_pair_are_merged(self) -> None:
        records = deduplicate(
            [
                Article(
                    title="Physiological limits shape lizard distributions: a preprint",
                    authors=["A. Researcher"],
                    published_date="2025-11-01",
                    doi="10.1101/preprint",
                    is_preprint=True,
                    sources=["biorxiv"],
                ),
                Article(
                    title="Physiological limits shape lizard distributions",
                    authors=["A. Researcher"],
                    published_date="2026-02-01",
                    doi="10.1000/published",
                    is_preprint=False,
                    sources=["openalex"],
                ),
            ],
            0.86,
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].doi, "10.1000/published")
        self.assertEqual(records[0].version_status, "published_with_preprint")
        self.assertIn("10.1101/preprint", records[0].related_dois)

    def test_source_failure_and_unavailable_booster_do_not_discard_success(self) -> None:
        def good(client, config, start, end):
            return [Article(title="Thermal ecology survives", sources=["openalex"])], False, ""

        def failed(client, config, start, end):
            raise RuntimeError("simulated failure")

        def unavailable(client, config, start, end):
            raise SourceUnavailable("simulated missing booster credential")

        config = json.loads(json.dumps(self.config))
        statuses = {}
        start = datetime(2026, 9, 28, tzinfo=timezone.utc)
        end = datetime(2026, 9, 29, tzinfo=timezone.utc)
        with patch.dict(
            COLLECTORS,
            {"openalex": good, "semantic_scholar": failed, "webofscience": unavailable},
        ):
            values = _run_sources(
                ["openalex", "semantic_scholar", "webofscience"],
                object(),  # type: ignore[arg-type]
                config,
                start,
                end,
                statuses,
            )
        self.assertEqual(len(values), 1)
        self.assertEqual(statuses["openalex"]["status"], "ok")
        self.assertEqual(statuses["semantic_scholar"]["status"], "failed")
        self.assertEqual(statuses["webofscience"]["status"], "unavailable")

    def test_citation_expansion_is_one_hop_and_deduplicates(self) -> None:
        class FakeClient:
            def get_json(self, source, url, params=None, headers=None):
                query_filter = str((params or {}).get("filter", ""))
                if query_filter.startswith("doi:"):
                    return {"results": [{"id": "https://openalex.org/WSEED", "referenced_works": ["https://openalex.org/WBACK"]}]}
                if query_filter.startswith("openalex_id:"):
                    return {"results": [{"id": "WBACK", "doi": "https://doi.org/10.1/back", "title": "Thermal physiology across life stages", "publication_date": "2020-01-01", "type": "article"}]}
                if query_filter.startswith("cites:"):
                    return {"results": [
                        {"id": "WFWD1", "doi": "https://doi.org/10.1/back", "title": "Thermal physiology across life stages", "publication_date": "2020-01-01", "type": "article"},
                        {"id": "WFWD2", "doi": "https://doi.org/10.1/fwd", "title": "Climate vulnerability of ectotherms", "publication_date": "2026-01-01", "type": "article"},
                    ]}
                return {"results": []}

        seed = normalize_article(
            Article(title="Seed thermal paper", doi="10.1/seed", relevance_class="direct_hit")
        )
        expanded, status = expand_openalex(FakeClient(), [seed], 6)  # type: ignore[arg-type]
        self.assertEqual(status["status"], "ok")
        unique = deduplicate(expanded, 0.94)
        self.assertEqual(len(unique), 2)
        self.assertEqual(
            {method for article in unique for method in article.discovery_methods},
            {"citation_backward", "citation_forward"},
        )

    def test_serendipity_requires_a_configured_bridge(self) -> None:
        plan = build_search_plan(self.config, mode="daily", report_date="2026-09-29")
        route = route_search_plan(plan, self.config)
        config = apply_route_to_config(self.config, plan, route)
        frontier_id = next(
            query["query_id"] for query in plan["queries"] if query["query_type"] == "frontier_transfer"
        )
        bridged = normalize_article(
            Article(
                title="Bayesian population forecasting for marine conservation",
                abstract="A state-space model supports adaptive management.",
                query_ids=[frontier_id],
            )
        )
        random = normalize_article(
            Article(title="Semiconductor fabrication optimization", query_ids=[frontier_id])
        )
        classify_relevance(score_article(bridged, config), config)
        classify_relevance(score_article(random, config), config)
        self.assertEqual(bridged.relevance_class, "serendipity")
        self.assertTrue(bridged.serendipity_bridges)
        self.assertNotEqual(random.relevance_class, "serendipity")

    def test_missing_abstract_stays_title_only(self) -> None:
        article = normalize_article(
            Article(title="Thermal tolerance in lizards", abstract="")
        )
        classify_relevance(score_article(article, self.config), self.config)
        self.assertEqual(article.evidence_level, "title-only")
        self.assertEqual(article.abstract, "")

    def test_coverage_reports_marginal_gain_without_exhaustiveness_claim(self) -> None:
        payload = {
            "mode": "daily",
            "window": {"lookback_hours": 72},
            "counts": {"searched": 5, "deduplicated": 3},
            "search_plan": {"queries": [
                {"query_id": "q1", "module": "a", "query_type": "direct"},
                {"query_id": "q2", "module": "a", "query_type": "method"},
            ]},
            "sources": {"openalex": {"status": "ok", "count": 5}},
            "retrieval_audit": {
                "raw_query_counts": {"q1": 3, "q2": 2},
                "records": [
                    {"key": "a", "title": "A", "sources": ["openalex"], "query_ids": ["q1"], "relevant": True},
                    {"key": "b", "title": "B", "sources": ["openalex"], "query_ids": ["q1", "q2"], "relevant": True},
                    {"key": "c", "title": "C", "sources": ["openalex"], "query_ids": ["q2"], "relevant": False},
                ],
            },
        }
        report = build_coverage_report(payload, self.config)
        self.assertEqual(report["queries"][0]["marginal_relevant_gain"], 2)
        self.assertEqual(report["queries"][1]["marginal_relevant_gain"], 0)
        self.assertIn("does not prove", report["operational_search_saturation"]["claim_boundary"])

    def test_crossref_verification_surfaces_mismatch_flags(self) -> None:
        record = {
            "title": "Thermal tolerance of lizards",
            "doi": "",
            "published_date": "2026-01-01",
            "version_status": "published_with_preprint",
        }
        item = {
            "DOI": "10.1/example",
            "title": ["Thermal tolerance of lizards"],
            "published": {"date-parts": [[2023, 1, 1]]},
            "container-title": ["Ecology"],
            "author": [{"given": "A", "family": "Researcher"}],
            "type": "journal-article",
        }
        result = evaluate_match(record, item, exact_doi_lookup=False)
        self.assertIn("doi_missing", result["flags"])
        self.assertIn("year_mismatch", result["flags"])
        self.assertIn("preprint_published_pair", result["flags"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
