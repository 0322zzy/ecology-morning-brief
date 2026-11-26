"""Bounded complements for failed journal/subject searches."""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date, datetime

from brief_engine import Article, RequestFailure, normalize_article, normalize_doi


PUBMED_ENDPOINT = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
PUBMED_QUERIES = {
    "physiology": '(("thermal tolerance"[Title/Abstract] OR "heat stress"[Title/Abstract] OR "Temperature"[MeSH Terms]) AND (ectotherm*[Title/Abstract] OR reptile*[Title/Abstract] OR amphibian*[Title/Abstract]))',
    "biology": '("population dynamics"[Title/Abstract] OR "life history"[Title/Abstract] OR "biological aging"[Title/Abstract]) AND (ecology[Title/Abstract] OR vertebrate*[Title/Abstract])',
}


def recovery_targets(config, plan, attempts, preliminary):
    """Choose a few journal or method gaps regardless of total core yield."""
    policy = config.get("search", {}).get("daily_recovery", {})
    failed = {qid for attempt in attempts if attempt["status"] in {"failed", "skipped"}
              for qid in attempt.get("query_ids", [])}
    screened = defaultdict(int)
    by_journal = defaultdict(int)
    for article in preliminary:
        if article.publication_status == "published":
            for qid in article.query_ids:
                screened[qid] += 1
                if qid.startswith("journal-"):
                    by_journal[qid] += 1
    capped = {
        qid for attempt in attempts if attempt.get("channel") == "journal_watch"
        and isinstance(attempt.get("reported_total"), int)
        and attempt.get("reported_total", 0) > (attempt.get("returned_records") or 0) + int(attempt.get("params", {}).get("offset", 0))
        for qid in attempt.get("query_ids", [])
    }
    exhausted = {
        qid for attempt in attempts if attempt.get("channel") == "journal_watch"
        and attempt["status"] == "ok" and isinstance(attempt.get("reported_total"), int)
        and attempt["reported_total"] <= (attempt.get("returned_records") or 0) + int(attempt.get("params", {}).get("offset", 0))
        for qid in attempt.get("query_ids", [])
    }
    journals = config.get("journal_watch", {}).get("journals", []) if plan.get("journal_watch_enabled") else []
    candidates = [j for j in journals if f"journal-{j['issn']}" in failed | (capped - exhausted)]
    candidates.sort(key=lambda j: (f"journal-{j['issn']}" not in failed, by_journal[f"journal-{j['issn']}"]))
    targets = [("", j["issn"], [f"journal-{j['issn']}"]) for j in candidates[:max(0, int(policy.get("max_journals", 3)))]]
    method_rows = [q for q in plan.get("queries", [])
                   if q["query_id"] in failed or (q.get("query_type") in {"method", "frontier_transfer"}
                                                  and screened[q["query_id"]] < int(policy.get("method_min_records", 3)))]
    method_rows = [q for q in method_rows if q.get("terms") and q.get("query")]
    method_rows.sort(key=lambda q: (q["query_id"] not in failed, screened[q["query_id"]]))
    modules = set()
    limit = max(0, int(policy.get("max_topic_queries", 2)))
    for q in method_rows:
        if not limit:
            break
        if q["module"] not in modules:
            targets.append((q["query"], "", [q["query_id"]]))
            modules.add(q["module"])
            limit -= 1
    return targets


def _record_attempt(client, ids, *, count=None, total=None, error=""):
    if getattr(client, "query_attempts", []):
        client.query_attempts[-1]["query_ids"] = ids
        client.note_page(count, total, error=error)


def parse_pubmed_xml(xml_text, query_ids):
    root = ET.fromstring(xml_text)
    if root.tag != "PubmedArticleSet" or root.findall(".//ERROR"):
        raise ValueError("Expected a PubmedArticleSet without an error")
    records = []
    for entry in root.findall("PubmedArticle"):
        pmid = entry.findtext("./MedlineCitation/PMID") or ""
        if pmid not in query_ids:
            continue
        article = entry.find("./MedlineCitation/Article")
        if article is None:
            continue
        title = "".join(article.find("./ArticleTitle").itertext()) if article.find("./ArticleTitle") is not None else ""
        doi = next((normalize_doi(el.text or "") for el in entry.findall("./PubmedData/ArticleIdList/ArticleId") if el.get("IdType") == "doi"), "")
        authors = [" ".join(filter(None, (a.findtext("ForeName"), a.findtext("LastName"))))
                   or a.findtext("CollectiveName", "") for a in article.findall("./AuthorList/Author")]
        abstract = " ".join("".join(el.itertext()) for el in article.findall("./Abstract/AbstractText"))
        online = ""
        for el in article.findall('./ArticleDate[@DateType="Electronic"]') + entry.findall('./PubmedData/History/PubMedPubDate[@PubStatus="epublish"]'):
            try:
                online = date(*(int(el.findtext(part, "")) for part in ("Year", "Month", "Day"))).isoformat()
                break
            except ValueError:
                continue
        journal_day = ""
        pub_date = article.find("./Journal/JournalIssue/PubDate")
        if pub_date is not None:
            parts = [pub_date.findtext(part, "") for part in ("Year", "Month", "Day")]
            for fmt in ("%Y %b %d", "%Y %m %d"):
                try:
                    journal_day = datetime.strptime(" ".join(parts), fmt).date().isoformat()
                    break
                except ValueError:
                    continue
        types = [el.text or "" for el in article.findall("./PublicationTypeList/PublicationType")]
        alerts = [f"{el.get('RefType')} (PubMed)" for el in entry.findall("./MedlineCitation/CommentsCorrectionsList/CommentsCorrections")
                  if el.get("RefType") in {"RetractionIn", "ExpressionOfConcernIn"}]
        if any(value in {"Retracted Publication", "Retraction of Publication"} for value in types):
            alerts.append("retracted (PubMed)")
        if not title:
            continue
        records.append(normalize_article(Article(
            title=title, authors=authors, abstract=abstract, abstract_source="PubMed author abstract",
            journal=article.findtext("./Journal/Title", ""), published_date=online or journal_day,
            online_date=online, doi=doi, url=f"https://doi.org/{doi}" if doi else f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            sources=["pubmed_supplement"], publication_type=", ".join(types),
            query_ids=query_ids[pmid], discovery_methods=["official_pubmed_complement"],
            publication_alerts=alerts,
        )))
    return records


def collect_pubmed(client, start, end, *, per_query=12):
    ids = defaultdict(list)
    errors = []
    succeeded = 0
    contact = os.getenv("CONTACT_EMAIL", "").strip()
    common = {"db": "pubmed", "tool": "EcologyMorningBrief"}
    if contact:
        common["email"] = contact
    for label, query in PUBMED_QUERIES.items():
        query_id = "pubmed-" + label
        params = dict(common, term=query, datetype="pdat", mindate=start.date().isoformat().replace("-", "/"),
                      maxdate=end.date().isoformat().replace("-", "/"), retmax=per_query, retmode="json",
                      sort="pub date")
        try:
            payload = client.get_json("pubmed_supplement", PUBMED_ENDPOINT + "esearch.fcgi", params)
        except RuntimeError as exc:
            errors.append(str(exc))
            continue
        result = payload.get("esearchresult") or {}
        try:
            found = result["idlist"]
            total = int(result["count"])
            if not isinstance(found, list) or any(not isinstance(pmid, str) or not pmid.isdigit() for pmid in found):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            error = "Missing or invalid PubMed search results"
            _record_attempt(client, [query_id], error=error)
            errors.append(error)
            continue
        _record_attempt(client, [query_id], count=len(found), total=total)
        succeeded += 1
        if total > len(found):
            errors.append(f"{query_id}: PubMed record budget reached")
        for pmid in found:
            ids[pmid].append(query_id)
    articles = []
    if ids:
        xml = client.get_text("pubmed_supplement", PUBMED_ENDPOINT + "efetch.fcgi",
                              dict(common, id=",".join(ids), retmode="xml"))
        try:
            articles = parse_pubmed_xml(xml, ids)
        except (ValueError, ET.ParseError) as exc:
            _record_attempt(client, [], error="Invalid PubMed article XML")
            raise RuntimeError("Invalid PubMed article XML") from exc
        parsed_root = ET.fromstring(xml)
        returned = len(parsed_root.findall("PubmedArticle"))
        _record_attempt(client, [], count=returned, total=len(ids))
        if returned < len(ids):
            errors.append("PubMed returned fewer articles than requested")
    if not succeeded and errors:
        raise RuntimeError("; ".join(dict.fromkeys(errors)))
    return articles, bool(errors), "; ".join(dict.fromkeys(errors))
