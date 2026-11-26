"""Regression fixtures for provider contracts, request audit and rendered evidence."""
import io
import json
import os
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from brief_engine import APIClient, Article, collect_crossref, collect_europepmc, load_config, merge_articles, normalize_article
from check_selection import check_selection
from fetch_candidates import main
from report_evidence import check_report
from supplemental_sources import parse_pubmed_xml, recovery_targets
from verify_metadata import verify_records


SKILL = Path(__file__).resolve().parents[1]
DAY = datetime(2026, 11, 26, tzinfo=timezone.utc)


class APIResponse(io.BytesIO):
    status = 200

    def __init__(self, payload):
        super().__init__(json.dumps(payload).encode())


class RetrievalAuditTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(SKILL / "config" / "interests.yaml")

    def test_retry_after_is_honored_with_redacted_attempt_records(self):
        url = "https://example.org/search"
        retry = urllib.error.HTTPError(url, 429, "Too Many Requests", {"Retry-After": "3"}, io.BytesIO())
        client = APIClient({"request_retries": 2, "request_backoff_seconds": 0.1})
        with patch("brief_engine.urllib.request.urlopen", side_effect=[retry, APIResponse({})]) as call:
            with patch("brief_engine.time.sleep") as sleep:
                client.get_json("test", url, {"api_key": "private-example", "query": "thermal ecology"})
        self.assertEqual(call.call_count, 2)
        sleep.assert_called_once_with(3.0)
        request, = client.query_attempts
        self.assertEqual(request["status"], "ok")
        self.assertEqual(request["params"], {"api_key": "[redacted]", "query": "thermal ecology"})
        self.assertEqual([a["http_status"] for a in request["attempts"]], [429, 200])

    def test_forbidden_request_does_not_retry(self):
        denied = urllib.error.HTTPError("https://example.org", 403, "Forbidden", {}, io.BytesIO())
        client = APIClient(self.config)
        with patch("brief_engine.urllib.request.urlopen", side_effect=denied) as call:
            with self.assertRaisesRegex(RuntimeError, "authentication_or_access"):
                client.get_json("test", "https://example.org")
        self.assertEqual(call.call_count, 1)

    def test_crossref_malformed_shard_does_not_erase_good_or_empty_shards(self):
        self.config["retrieval_queries"] = ["wrong shape", "zero", "later good"]
        self.config["sources"]["crossref"]["endpoint"] = "https://example.org/works"
        client = APIClient(self.config)
        payloads = [
            {"message": {"renamed": []}},
            {"message": {"items": [], "total-results": 0}},
            {"message": {"items": [{"title": ["Publication dated by its journal"], "DOI": "10.9999/fixture",
                                    "container-title": ["Ecography"], "type": "journal-article",
                                    "published-online": {"date-parts": [[2026, 11]]}}], "total-results": 1}},
        ]
        with patch("brief_engine.urllib.request.urlopen", side_effect=map(APIResponse, payloads)):
            rows, partial, detail = collect_crossref(client, self.config, DAY, DAY)
        self.assertEqual([a.doi for a in rows], ["10.9999/fixture"])
        self.assertEqual(rows[0].published_date, "")
        self.assertTrue(partial)
        self.assertIn("message.items", detail)
        self.assertEqual([a["status"] for a in client.query_attempts], ["failed", "ok", "ok"])
        self.assertEqual(client.query_attempts[1]["returned_records"], 0)

    def test_europepmc_interrupted_pagination_preserves_first_page(self):
        self.config["europepmc_queries"] = ["thermal biology"]
        client = APIClient(self.config)
        first = {"hitCount": 2, "nextCursorMark": "cursor-two", "resultList": {"result": [{"title": "First record", "doi": "10.9999/first"}]}}
        with patch("brief_engine.urllib.request.urlopen", side_effect=[APIResponse(first), APIResponse({"hitCount": 2})]):
            rows, partial, detail = collect_europepmc(client, self.config, DAY, DAY)
        self.assertEqual([a.doi for a in rows], ["10.9999/first"])
        self.assertTrue(partial)
        self.assertIn("resultList.result", detail)
        self.assertEqual(client.query_attempts[1]["params"]["cursorMark"], "cursor-two")

    def test_recovery_for_failed_journal_even_with_abundant_core_candidates(self):
        self.config["search"]["daily_recovery"] = {"max_journals": 1, "max_topic_queries": 0}
        journal = self.config["journal_watch"]["journals"][0]
        query = f"journal-{journal['issn']}"
        attempts = [{"status": "failed", "channel": "journal_watch", "query_ids": [query]}]
        prelim = [Article("Another paper", rule_score=99, publication_status="published")] * 1000
        self.assertEqual(recovery_targets(self.config, {"journal_watch_enabled": True}, attempts, prelim),
                         [("", journal["issn"], [query])])

    def test_fetch_cli_writes_replayable_snapshot_and_zero_record_audit(self):
        with tempfile.TemporaryDirectory() as temp:
            tmp = Path(temp)
            config = self.config
            config["context_refresh"]["enabled"] = False
            config["sources"]["crossref"]["endpoint"] = "https://example.org/works"
            config_file = tmp / "config.json"
            config_file.write_text(json.dumps(config))
            path = tmp / "run.json"
            args = ["fetch_candidates", "--config", str(config_file), "--state-dir", str(tmp / "state"),
                    "--source", "crossref", "--date", "2026-11-26", "--output", str(path)]
            with patch("sys.argv", args), patch("brief_engine.urllib.request.urlopen",
                    side_effect=lambda *a, **kw: APIResponse({"message": {"items": [], "total-results": 0}})):
                self.assertEqual(main(), 0)
            run = load_config(path)
            audit = load_config(path.with_suffix(".audit.json"))
            self.assertEqual(run["run_snapshot"]["sha256"], audit["snapshot"]["snapshot_sha256"])
            self.assertEqual(run["search_plan"], audit["snapshot"]["plan"])
            stats = audit["source_counts"]["crossref"]
            self.assertGreater(stats["successful_pages"], 0)
            self.assertEqual(stats["api_returned_records"], 0)
            self.assertEqual(audit["counts"]["candidate"], 0)

    def test_pubmed_parser_does_not_invent_a_day_and_preserves_retraction_link(self):
        xml = """<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>1</PMID>
        <Article><ArticleTitle>Thermal biology</ArticleTitle><Journal><Title>Ecography</Title>
        <JournalIssue><PubDate><Year>2026</Year><Month>Nov</Month></PubDate></JournalIssue></Journal>
        <Abstract><AbstractText>Test abstract.</AbstractText></Abstract>
        <PublicationTypeList><PublicationType>Journal Article</PublicationType></PublicationTypeList></Article>
        <CommentsCorrectionsList><CommentsCorrections RefType="RetractionIn" /></CommentsCorrectionsList>
        </MedlineCitation></PubmedArticle></PubmedArticleSet>"""
        row, = parse_pubmed_xml(xml, {"1": ["fixture"]})
        self.assertFalse(row.published_date)
        self.assertEqual(row.abstract_source, "PubMed author abstract")
        self.assertFalse(check_selection([row.serializable()], self.config)["ok"])

    def test_formal_version_does_not_borrow_preprint_abstract_or_date(self):
        preprint = normalize_article(Article("Same title", journal="bioRxiv", doi="10.1101/test",
                                             abstract="Earlier version", published_date="2026-11-01"))
        published = normalize_article(Article("Same title", journal="Ecography", doi="10.9999/formal",
                                              publication_type="journal-article"))
        merged = merge_articles(preprint, published)
        self.assertEqual(merged.doi, published.doi)
        self.assertEqual(merged.abstract, "")
        self.assertEqual(merged.published_date, "")

    def test_metadata_enrichment_rejects_mismatched_abstract(self):
        client = APIClient(self.config)
        record = {"title": "Article", "doi": "10.9999/article"}
        with patch.dict(os.environ, {}, clear=True), patch("brief_engine.urllib.request.urlopen", side_effect=map(APIResponse, [
            {"message": {"title": ["Article"], "DOI": record["doi"]}},
            {"doi": "https://doi.org/10.9999/different", "abstract_inverted_index": {"Unsupported": [0]}},
            {"hitCount": 0, "resultList": {"result": []}},
        ])):
            rows = verify_records(client, [record], max_abstract_lookups=1)
        self.assertFalse(rows[0].get("abstract"))

    def test_report_requires_real_evidence_and_flags_unsupported_precision(self):
        row = {"doi": "10.9999/fixture", "title": "Shallow wetland ecology", "abstract":
               "We measured 24 ponds. The decrease was 10%."}
        heading = "## 今日重点\n\n### 01. [Shallow wetland ecology](https://doi.org/10.9999/fixture)\n\n"
        prose = "\n\n测量了24个池塘，结果下降10%。图2报告样本量为900。\n"
        result = check_report([row], heading + "作者｜[证据：作者摘要]" + prose)
        self.assertTrue(result["ok"])
        self.assertEqual(result["needs_review"][0]["numbers"], ["900"])
        self.assertEqual(result["needs_review"][0]["figure_refs"], ["图2"])
        self.assertFalse(check_report([row], heading + "作者" + prose)["ok"])
        self.assertFalse(check_report([row], heading + "作者｜[证据：开放全文]" + prose)["ok"])
