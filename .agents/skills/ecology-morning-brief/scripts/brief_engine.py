#!/usr/bin/env python3
"""Deterministic retrieval and state helpers for the Ecology Morning Brief skill."""

from __future__ import annotations

import html
import json
import logging
import os
import random
import re
import sqlite3
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable


LOGGER = logging.getLogger("ecology_morning_brief")


@dataclass
class Article:
    title: str
    authors: list[str] = field(default_factory=list)
    abstract: str = ""
    journal: str = ""
    published_date: str = ""
    online_date: str = ""
    indexed_date: str = ""
    doi: str = ""
    url: str = ""
    sources: list[str] = field(default_factory=list)
    publication_type: str = "article"
    keywords: list[str] = field(default_factory=list)
    subjects: list[str] = field(default_factory=list)
    is_preprint: bool = False
    related_dois: list[str] = field(default_factory=list)
    normalized_title: str = ""
    rule_score: float = 0.0
    category: str = "Broader ecology"
    matched_topics: list[str] = field(default_factory=list)
    previously_reported: bool = False
    query_ids: list[str] = field(default_factory=list)
    discovery_methods: list[str] = field(default_factory=list)
    citation_seed_ids: list[str] = field(default_factory=list)
    relevance_class: str = "unclassified"
    serendipity_bridges: list[str] = field(default_factory=list)
    evidence_level: str = "title-only"
    metadata_verification: dict[str, Any] = field(default_factory=dict)
    version_status: str = "canonical"
    publication_status: str = "unverified"

    def serializable(self) -> dict[str, Any]:
        return asdict(self)


def load_config(path: Path) -> dict[str, Any]:
    """Load JSON-compatible YAML without third-party dependencies."""
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Configuration root must be an object: {path}")
    return value


def configure_logging(state_dir: Path) -> Path:
    log_path = state_dir / "logs" / "collector.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    LOGGER.setLevel(logging.INFO)
    LOGGER.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    LOGGER.addHandler(file_handler)
    return log_path


def normalize_doi(value: str) -> str:
    doi = (value or "").strip().casefold()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix) :]
    doi = doi.strip().rstrip(".,;)")
    if doi in {"na", "n/a", "none", "null", "not available"}:
        return ""
    return doi


def normalize_title(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").casefold()
    text = "".join(" " if unicodedata.category(char).startswith("P") else char for char in text)
    return re.sub(r"\s+", " ", text).strip()


def clean_markup(value: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value or "")).split())


def normalize_article(article: Article) -> Article:
    article.title = " ".join((article.title or "").split())
    article.abstract = clean_markup(article.abstract)
    article.journal = clean_markup(article.journal)
    article.doi = normalize_doi(article.doi)
    article.related_dois = sorted(
        {normalize_doi(value) for value in article.related_dois if normalize_doi(value)}
    )
    article.normalized_title = normalize_title(article.title)
    article.authors = list(dict.fromkeys(x.strip() for x in article.authors if x.strip()))
    article.sources = sorted(set(x for x in article.sources if x))
    article.keywords = list(dict.fromkeys(x.strip() for x in article.keywords if x.strip()))
    article.subjects = list(dict.fromkeys(x.strip() for x in article.subjects if x.strip()))
    article.query_ids = list(dict.fromkeys(x.strip() for x in article.query_ids if x.strip()))
    article.discovery_methods = list(
        dict.fromkeys(x.strip() for x in article.discovery_methods if x.strip())
    )
    article.citation_seed_ids = list(
        dict.fromkeys(x.strip() for x in article.citation_seed_ids if x.strip())
    )
    article.serendipity_bridges = list(
        dict.fromkeys(x.strip() for x in article.serendipity_bridges if x.strip())
    )
    article.evidence_level = "abstract-supported" if article.abstract else "title-only"
    repository = any(term in article.journal.casefold() for term in
                     ("biorxiv", "medrxiv", "arxiv", "research square", "preprint"))
    article.is_preprint = bool(article.is_preprint or repository
                              or "preprint" in article.publication_type.casefold()
                              or article.doi.startswith(("10.1101/", "10.21203/", "10.48550/")))
    formal_type = any(term in article.publication_type.casefold() for term in
                      ("journal", "review", "research article"))
    article.publication_status = ("preprint" if article.is_preprint else
                                  "published" if article.journal and
                                  (formal_type or "crossref" in article.sources or
                                   "journal_watch" in article.discovery_methods) else "unverified")
    return article


def planned_query_ids(config: dict[str, Any], source: str, query: str = "__source_scan__") -> list[str]:
    lookup = config.get("_search_plan", {}).get("query_lookup", {}).get(source, {})
    return list(lookup.get(query.casefold(), lookup.get("__source_scan__", [])))


def parse_date(value: str) -> datetime | None:
    if not value:
        return None
    clean = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(clean)
    except ValueError:
        try:
            parsed = datetime.strptime(clean[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def article_in_window(article: Article, start: datetime, end: datetime) -> bool:
    raw_dates = [article.online_date, article.published_date]
    if not any(raw_dates):
        raw_dates = [article.indexed_date]
    for raw in raw_dates:
        parsed = parse_date(raw)
        if parsed is None:
            continue
        if len(raw.strip()) == 10 and start.date() <= parsed.date() <= end.date():
            return True
        if len(raw.strip()) != 10 and start <= parsed <= end:
            return True
    return False


class SourceUnavailable(RuntimeError):
    """A configured source cannot run until its credential is provided."""


class APIClient:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.query_attempts: list[dict[str, Any]] = []
        self.timeout = float(config.get("request_timeout_seconds", 20))
        self.retries = int(config.get("request_retries", 3))
        self.backoff = float(config.get("request_backoff_seconds", 1.0))
        contact = os.getenv("CONTACT_EMAIL", "unknown@example.org")
        self.user_agent = os.getenv(
            "HTTP_USER_AGENT", f"EcologyMorningBrief-CodexSkill/1.0 (mailto:{contact})"
        )

    def get_json(
        self,
        source: str,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        query = str((params or {}).get("query.bibliographic", (params or {}).get("query", (params or {}).get("search", "__source_scan__"))))
        attempt_record = {"source": source, "query_ids": planned_query_ids(self.config, source, query), "status": "failed"}
        self.query_attempts.append(attempt_record)
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            started = time.monotonic()
            request_headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
            request_headers.update(headers or {})
            request = urllib.request.Request(url, headers=request_headers)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    status = getattr(response, "status", 200)
                    payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("API response is not a JSON object")
                LOGGER.info(
                    "request source=%s status=%s attempt=%s elapsed=%.2fs",
                    source,
                    status,
                    attempt,
                    time.monotonic() - started,
                )
                attempt_record["status"] = "ok"
                return payload
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
                error = exc
                LOGGER.warning(
                    "request_failed source=%s attempt=%s/%s error=%s",
                    source,
                    attempt,
                    self.retries,
                    exc,
                )
                if attempt < self.retries:
                    time.sleep(self.backoff * (2 ** (attempt - 1)) + random.uniform(0, 0.25))
        raise RuntimeError(f"{source} failed after {self.retries} attempts: {error}")

    def get_text(
        self,
        source: str,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> str:
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            request_headers = {"User-Agent": self.user_agent, "Accept": "application/atom+xml,text/xml"}
            request_headers.update(headers or {})
            try:
                with urllib.request.urlopen(
                    urllib.request.Request(url, headers=request_headers), timeout=self.timeout
                ) as response:
                    return response.read().decode("utf-8")
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, UnicodeDecodeError) as exc:
                error = exc
                LOGGER.warning(
                    "request_failed source=%s attempt=%s/%s error=%s",
                    source,
                    attempt,
                    self.retries,
                    exc,
                )
                if attempt < self.retries:
                    time.sleep(self.backoff * (2 ** (attempt - 1)) + random.uniform(0, 0.25))
        raise RuntimeError(f"{source} failed after {self.retries} attempts: {error}")


def _date_from_crossref(item: dict[str, Any], field_name: str) -> str:
    parts = (item.get(field_name) or {}).get("date-parts", [])
    if not parts or not parts[0]:
        return ""
    values = list(parts[0]) + [1, 1]
    try:
        return f"{int(values[0]):04d}-{int(values[1]):02d}-{int(values[2]):02d}"
    except (TypeError, ValueError, IndexError):
        return ""


def collect_crossref(
    client: APIClient, config: dict[str, Any], start: datetime, end: datetime
) -> tuple[list[Article], bool, str]:
    source_config = config["sources"]["crossref"]
    rows = int(source_config.get("rows_per_query", 40))
    maximum = int(source_config.get("max_articles", 200))
    contact = os.getenv("CONTACT_EMAIL", "")
    articles: list[Article] = []
    seen: set[str] = set()
    partial, last_error = False, ""
    for query in config.get("retrieval_queries", []):
        params: dict[str, Any] = {
            "query.bibliographic": query,
            "filter": (
                f"from-pub-date:{start.date().isoformat()},"
                f"until-pub-date:{end.date().isoformat()},type:journal-article"
            ),
            "rows": rows,
            "sort": "published",
            "order": "desc",
        }
        if contact:
            params["mailto"] = contact
        params.update(source_config.get("extra_params", {}))
        try:
            payload = client.get_json("crossref", source_config.get("endpoint", "https://api.crossref.org/works"), params)
        except RuntimeError as exc:
            if not articles:
                raise
            partial, last_error = True, str(exc)
            break
        for item in (payload.get("message") or {}).get("items", []):
            doi = normalize_doi(str(item.get("DOI", "")))
            if doi and doi in seen:
                continue
            if doi:
                seen.add(doi)
            title_value = item.get("title") or []
            journal_value = item.get("container-title") or []
            authors = []
            for author in item.get("author", []):
                name = " ".join(
                    part for part in (author.get("given", ""), author.get("family", "")) if part
                ).strip()
                if name:
                    authors.append(name)
            related: list[str] = []
            for entries in (item.get("relation") or {}).values():
                for entry in entries or []:
                    if entry.get("id-type") == "doi":
                        related.append(str(entry.get("id", "")))
            online = _date_from_crossref(item, "published-online")
            published = (
                online
                or _date_from_crossref(item, "published-print")
                or _date_from_crossref(item, "published")
                or _date_from_crossref(item, "created")
            )
            articles.append(
                normalize_article(
                    Article(
                        title=str(title_value[0]) if title_value else "",
                        authors=authors,
                        abstract=str(item.get("abstract", "")),
                        journal=str(journal_value[0]) if journal_value else "",
                        published_date=published,
                        online_date=online,
                        indexed_date=str((item.get("indexed") or {}).get("date-time", "")),
                        doi=doi,
                        url=str(item.get("URL", "")),
                        sources=["crossref"],
                        publication_type=str(item.get("type", "article")),
                        subjects=[str(x) for x in item.get("subject", [])],
                        related_dois=related,
                        query_ids=planned_query_ids(config, "crossref", str(query)),
                        discovery_methods=["keyword"],
                    )
                )
            )
            if len(articles) >= maximum:
                return articles, partial, last_error
    return articles, partial, last_error


def collect_biorxiv(
    client: APIClient, config: dict[str, Any], start: datetime, end: datetime
) -> tuple[list[Article], bool, str]:
    source_config = config["sources"]["biorxiv"]
    maximum = int(source_config.get("max_articles", 300))
    max_pages = max(1, int(source_config.get("max_pages", 8)))
    categories = {str(x).casefold() for x in source_config.get("categories", [])}
    cursor = 0
    pages = 0
    articles: list[Article] = []
    partial, last_error = False, ""
    while len(articles) < maximum and pages < max_pages:
        url = (
            "https://api.biorxiv.org/details/biorxiv/"
            f"{start.date().isoformat()}/{end.date().isoformat()}/{cursor}"
        )
        try:
            payload = client.get_json("biorxiv", url)
        except RuntimeError as exc:
            if not articles:
                raise
            partial, last_error = True, str(exc)
            break
        collection = payload.get("collection", [])
        pages += 1
        if not collection:
            break
        for item in collection:
            category = str(item.get("category", ""))
            if categories and category.casefold() not in categories:
                continue
            doi = normalize_doi(str(item.get("doi", "")))
            published_doi = normalize_doi(str(item.get("published", "")))
            articles.append(
                normalize_article(
                    Article(
                        title=str(item.get("title", "")),
                        authors=[x.strip() for x in str(item.get("authors", "")).split(";") if x.strip()],
                        abstract=str(item.get("abstract", "")),
                        journal="bioRxiv",
                        published_date=str(item.get("date", "")),
                        online_date=str(item.get("date", "")),
                        doi=doi,
                        url=f"https://doi.org/{doi}" if doi else "",
                        sources=["biorxiv"],
                        publication_type="preprint",
                        subjects=[category] if category else [],
                        is_preprint=True,
                        related_dois=[published_doi] if published_doi else [],
                        query_ids=planned_query_ids(config, "biorxiv"),
                        discovery_methods=["source_scan", "preprint"],
                    )
                )
            )
            if len(articles) >= maximum:
                break
        messages = payload.get("messages", [])
        total = int(messages[0].get("total", 0)) if messages else len(collection)
        cursor += len(collection)
        if cursor >= total:
            break
    if pages >= max_pages and cursor < total:
        partial = True
        last_error = f"page budget reached ({max_pages}) before source exhaustion"
    return articles, partial, last_error


def collect_europepmc(
    client: APIClient, config: dict[str, Any], start: datetime, end: datetime
) -> tuple[list[Article], bool, str]:
    source_config = config["sources"]["europepmc"]
    page_size = int(source_config.get("page_size", 100))
    maximum = int(source_config.get("max_articles", 200))
    queries = list(config.get("europepmc_queries", [])) or [str(config.get("europepmc_query", ""))]
    per_query = int(source_config.get("max_articles_per_query", maximum))
    max_pages = int(source_config.get("max_pages_per_query", 20))
    articles: list[Article] = []
    partial, errors = False, []
    succeeded = 0
    # Divide capacity before fetching: one broad query cannot consume every slot.
    per_query = min(per_query, max(1, maximum // max(1, len(queries))))
    for topic_query in queries:
        query = (f"FIRST_PDATE:[{start.date().isoformat()} TO {end.date().isoformat()}] "
                 f"AND ({topic_query})")
        if source_config.get("published_only", False):
            query += " AND NOT SRC:PPR AND NOT PUB_TYPE:Preprint"
        cursor, fetched = "*", 0
        for page in range(max_pages):
            if fetched >= per_query or len(articles) >= maximum:
                partial = True
                break
            params = {"query": query + " sort_date:y", "format": "json", "resultType": "core",
                      "pageSize": min(page_size, per_query-fetched, maximum-len(articles)), "cursorMark": cursor}
            try:
                # Use the unwrapped topic query for exact plan provenance.
                lookup = config.get("_search_plan", {}).get("query_lookup", {}).get("europepmc", {})
                lookup[params["query"].casefold()] = planned_query_ids(config, "europepmc", topic_query)
                payload = client.get_json("europepmc", "https://www.ebi.ac.uk/europepmc/webservices/rest/search", params)
                succeeded += 1
            except RuntimeError as exc:
                partial = True
                errors.append(str(exc))
                break
            results = (payload.get("resultList") or {}).get("result", [])
            if not results:
                break
            for item in results:
                doi = normalize_doi(str(item.get("doi", "")))
                authors = [
                    str(author.get("fullName", "")).strip()
                    for author in (item.get("authorList") or {}).get("author", [])
                    if author.get("fullName")
                ]
                pub_types = [str(x) for x in (item.get("pubTypeList") or {}).get("pubType", [])]
                is_preprint = str(item.get("source", "")).upper() == "PPR" or any("preprint" in x.casefold() for x in pub_types)
                journal_data = ((item.get("journalInfo") or {}).get("journal") or {})
                journal = str(
                    item.get("journalTitle")
                    or journal_data.get("title")
                    or journal_data.get("medlineAbbreviation")
                    or ""
                )
                articles.append(
                    normalize_article(
                        Article(
                            title=str(item.get("title", "")),
                            authors=authors,
                            abstract=str(item.get("abstractText", "")),
                            journal=journal,
                            published_date=str(
                                item.get("firstPublicationDate")
                                or item.get("electronicPublicationDate")
                                or ""
                            ),
                            online_date=str(item.get("electronicPublicationDate", "")),
                            indexed_date=str(item.get("dateOfCompletion", "")),
                            doi=doi,
                            url=(
                                f"https://doi.org/{doi}"
                                if doi
                                else f"https://europepmc.org/article/{item.get('source', '')}/{item.get('id', '')}"
                            ),
                            sources=["europepmc"],
                            publication_type=", ".join(pub_types) or "article",
                            keywords=[str(x) for x in (item.get("keywordList") or {}).get("keyword", [])],
                            is_preprint=is_preprint,
                            query_ids=planned_query_ids(config, "europepmc", topic_query),
                            discovery_methods=["keyword"],
                        )
                    )
                )
            fetched += len(results)
            next_cursor = str(payload.get("nextCursorMark", ""))
            remaining = int(payload.get("hitCount", fetched)) > fetched
            if not next_cursor or next_cursor == cursor or not remaining:
                break
            if page + 1 >= max_pages or fetched >= per_query:
                partial = True
                errors.append("Europe PMC per-query page/record budget reached")
                break
            cursor = next_cursor
    if not succeeded and errors:
        raise RuntimeError(errors[0])
    return articles[:maximum], partial, "; ".join(dict.fromkeys(errors))


def _openalex_abstract(inverted_index: Any) -> str:
    """Reconstruct OpenAlex's position-indexed abstract without inventing text."""
    if not isinstance(inverted_index, dict):
        return ""
    positioned: list[tuple[int, str]] = []
    for word, raw_positions in inverted_index.items():
        if not isinstance(raw_positions, list):
            continue
        for raw_position in raw_positions:
            try:
                positioned.append((int(raw_position), str(word)))
            except (TypeError, ValueError):
                continue
    return " ".join(word for _, word in sorted(positioned))


def collect_openalex(
    client: APIClient, config: dict[str, Any], start: datetime, end: datetime
) -> tuple[list[Article], bool, str]:
    """Collect recent works from OpenAlex; authentication is optional."""
    source_config = config["sources"]["openalex"]
    page_size = min(100, max(1, int(source_config.get("page_size", 50))))
    maximum = int(source_config.get("max_articles", 250))
    max_pages = max(1, int(source_config.get("max_pages_per_query", 1)))
    max_queries = max(1, int(source_config.get("max_queries", 5)))
    queries = list(config.get("openalex_queries", []))
    if not queries:
        queries = list(config.get("retrieval_queries", []))
    api_key = os.getenv("OPENALEX_API_KEY", "").strip()
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
    articles: list[Article] = []
    seen: set[str] = set()
    partial, last_error = False, ""
    for query in queries[:max_queries]:
        for page in range(1, max_pages + 1):
            params = {
                "search": query,
                "filter": (
                    f"from_publication_date:{start.date().isoformat()},"
                    f"to_publication_date:{end.date().isoformat()}"
                ),
                "sort": "publication_date:desc",
                "per_page": min(page_size, maximum - len(articles)),
                "page": page,
            }
            try:
                payload = client.get_json(
                    "openalex", "https://api.openalex.org/works", params, headers=headers
                )
            except RuntimeError as exc:
                if not articles:
                    raise
                partial, last_error = True, str(exc)
                break
            results = payload.get("results", [])
            if not isinstance(results, list):
                raise RuntimeError("openalex response has no results list")
            for item in results:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or item.get("display_name") or "")
                doi = normalize_doi(str(item.get("doi", "")))
                key = doi or str(item.get("id", "")) or normalize_title(title)
                if not key or key in seen:
                    continue
                seen.add(key)
                authors = []
                for authorship in item.get("authorships", []) or []:
                    if not isinstance(authorship, dict):
                        continue
                    author = authorship.get("author") or {}
                    name = str(author.get("display_name", "")).strip()
                    if name:
                        authors.append(name)
                primary_location = item.get("primary_location") or {}
                source = primary_location.get("source") or {}
                landing_page = str(primary_location.get("landing_page_url", ""))
                topics = [
                    str(topic.get("display_name", ""))
                    for topic in item.get("topics", []) or []
                    if isinstance(topic, dict) and topic.get("display_name")
                ]
                keywords = [
                    str(keyword.get("display_name", ""))
                    for keyword in item.get("keywords", []) or []
                    if isinstance(keyword, dict) and keyword.get("display_name")
                ]
                work_type = str(item.get("type", "article"))
                articles.append(
                    normalize_article(
                        Article(
                            title=title,
                            authors=authors,
                            abstract=_openalex_abstract(
                                item.get("abstract_inverted_index")
                            ),
                            journal=str(source.get("display_name", "")),
                            published_date=str(item.get("publication_date", "")),
                            online_date=str(item.get("publication_date", "")),
                            doi=doi,
                            url=(
                                f"https://doi.org/{doi}"
                                if doi
                                else landing_page or str(item.get("id", ""))
                            ),
                            sources=["openalex"],
                            publication_type=work_type,
                            keywords=keywords,
                            subjects=topics,
                            is_preprint=work_type.casefold() == "preprint",
                            query_ids=planned_query_ids(config, "openalex", str(query)),
                            discovery_methods=["keyword"],
                        )
                    )
                )
                if len(articles) >= maximum:
                    return articles, partial, last_error
            if len(results) < page_size:
                break
        if partial:
            break
    return articles, partial, last_error


def collect_semantic_scholar(
    client: APIClient, config: dict[str, Any], start: datetime, end: datetime
) -> tuple[list[Article], bool, str]:
    """Collect recent relevance-ranked papers from Semantic Scholar."""
    source_config = config["sources"]["semantic_scholar"]
    page_size = min(100, max(1, int(source_config.get("page_size", 50))))
    maximum = int(source_config.get("max_articles", 250))
    max_pages = max(1, int(source_config.get("max_pages_per_query", 1)))
    max_queries = max(1, int(source_config.get("max_queries", 5)))
    request_interval = max(
        0.0, float(source_config.get("request_interval_seconds", 0.0))
    )
    queries = list(config.get("semantic_scholar_queries", []))
    if not queries:
        queries = list(config.get("retrieval_queries", []))
    api_key = (
        os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip()
        or os.getenv("S2_API_KEY", "").strip()
    )
    headers = {"x-api-key": api_key} if api_key else None
    fields = (
        "title,abstract,authors,venue,publicationDate,year,externalIds,url,"
        "publicationTypes,fieldsOfStudy"
    )
    articles: list[Article] = []
    seen: set[str] = set()
    partial, last_error = False, ""
    request_count = 0
    for raw_query in queries[:max_queries]:
        offset = 0
        query = str(raw_query).replace("-", " ")
        for _ in range(max_pages):
            if request_count and request_interval:
                time.sleep(request_interval)
            params = {
                "query": query,
                "publicationDateOrYear": (
                    f"{start.date().isoformat()}:{end.date().isoformat()}"
                ),
                "fields": fields,
                "limit": min(page_size, maximum - len(articles)),
                "offset": offset,
            }
            try:
                payload = client.get_json(
                    "semantic_scholar",
                    "https://api.semanticscholar.org/graph/v1/paper/search",
                    params,
                    headers=headers,
                )
                request_count += 1
            except RuntimeError as exc:
                if not articles:
                    raise
                partial, last_error = True, str(exc)
                break
            results = payload.get("data", [])
            if not isinstance(results, list):
                raise RuntimeError("semantic_scholar response has no data list")
            for item in results:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title", ""))
                external_ids = item.get("externalIds") or {}
                doi = normalize_doi(str(external_ids.get("DOI", "")))
                key = doi or str(item.get("paperId", "")) or normalize_title(title)
                if not key or key in seen:
                    continue
                seen.add(key)
                authors = [
                    str(author.get("name", "")).strip()
                    for author in item.get("authors", []) or []
                    if isinstance(author, dict) and author.get("name")
                ]
                publication_types = [
                    str(value) for value in item.get("publicationTypes", []) or []
                ]
                articles.append(
                    normalize_article(
                        Article(
                            title=title,
                            authors=authors,
                            abstract=str(item.get("abstract") or ""),
                            journal=str(item.get("venue") or ""),
                            published_date=str(
                                item.get("publicationDate") or item.get("year") or ""
                            ),
                            online_date=str(item.get("publicationDate") or ""),
                            doi=doi,
                            url=str(item.get("url") or ""),
                            sources=["semantic_scholar"],
                            publication_type=", ".join(publication_types) or "article",
                            subjects=[
                                str(value)
                                for value in item.get("fieldsOfStudy", []) or []
                            ],
                            query_ids=planned_query_ids(
                                config, "semantic_scholar", str(raw_query)
                            ),
                            discovery_methods=["keyword"],
                        )
                    )
                )
                if len(articles) >= maximum:
                    return articles, partial, last_error
            if len(results) < page_size:
                break
            next_offset = payload.get("next")
            if next_offset is None:
                break
            try:
                offset = int(next_offset)
            except (TypeError, ValueError):
                break
        if partial:
            break
    return articles, partial, last_error


def _arxiv_search_query(value: str, categories: list[str]) -> str:
    parts = re.findall(r'"([^"]+)"|([^\s]+)', value)
    terms = [left or right for left, right in parts if (left or right)]
    clauses = [f'all:"{term}"' if " " in term else f"all:{term}" for term in terms[:8]]
    category_clause = " OR ".join(f"cat:{category}" for category in categories if category)
    query_clause = " AND ".join(clauses) or "all:ecology"
    return f"({query_clause}) AND ({category_clause})" if category_clause else query_clause


def collect_arxiv(
    client: APIClient, config: dict[str, Any], start: datetime, end: datetime
) -> tuple[list[Article], bool, str]:
    """Collect routed q-bio preprints from arXiv's public Atom API."""
    source_config = config["sources"]["arxiv"]
    maximum = max(1, int(source_config.get("max_articles", 80)))
    max_queries = max(1, int(source_config.get("max_queries", 3)))
    interval = max(0.0, float(source_config.get("request_interval_seconds", 3.0)))
    categories = [str(value) for value in source_config.get("categories", [])]
    queries = list(config.get("arxiv_queries", []))[:max_queries]
    articles: list[Article] = []
    seen: set[str] = set()
    partial, last_error = False, ""
    namespace = {
        "atom": "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
    }
    for query_index, query in enumerate(queries):
        if query_index and interval:
            time.sleep(interval)
        params = {
            "search_query": _arxiv_search_query(str(query), categories),
            "start": 0,
            "max_results": min(50, maximum - len(articles)),
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        try:
            payload = client.get_text("arxiv", "https://export.arxiv.org/api/query", params)
            root = ET.fromstring(payload)
        except (RuntimeError, ET.ParseError) as exc:
            if not articles:
                raise RuntimeError(str(exc)) from exc
            partial, last_error = True, str(exc)
            break
        for entry in root.findall("atom:entry", namespace):
            title = clean_markup(entry.findtext("atom:title", default="", namespaces=namespace))
            arxiv_url = entry.findtext("atom:id", default="", namespaces=namespace)
            arxiv_id = arxiv_url.rstrip("/").rsplit("/", 1)[-1]
            doi = normalize_doi(entry.findtext("arxiv:doi", default="", namespaces=namespace))
            key = doi or arxiv_id or normalize_title(title)
            if not key or key in seen:
                continue
            seen.add(key)
            authors = [
                clean_markup(author.findtext("atom:name", default="", namespaces=namespace))
                for author in entry.findall("atom:author", namespace)
            ]
            published = entry.findtext("atom:published", default="", namespaces=namespace)
            subjects = [
                str(category.attrib.get("term", ""))
                for category in entry.findall("atom:category", namespace)
            ]
            journal_ref = entry.findtext("arxiv:journal_ref", default="", namespaces=namespace)
            articles.append(
                normalize_article(
                    Article(
                        title=title,
                        authors=authors,
                        abstract=entry.findtext("atom:summary", default="", namespaces=namespace),
                        journal=clean_markup(journal_ref) or "arXiv",
                        published_date=published,
                        online_date=published,
                        doi=doi,
                        url=arxiv_url,
                        sources=["arxiv"],
                        publication_type="preprint",
                        subjects=subjects,
                        is_preprint=True,
                        query_ids=planned_query_ids(config, "arxiv", str(query)),
                        discovery_methods=["keyword", "preprint"],
                    )
                )
            )
            if len(articles) >= maximum:
                return articles, partial, last_error
    return articles, partial, last_error


def _year_month(year: Any, month: Any) -> str:
    """Return the most precise ISO-like publication date exposed by WoS Starter."""
    try:
        year_value = int(year)
    except (TypeError, ValueError):
        return ""
    month_text = str(month or "").strip()
    if not month_text:
        return f"{year_value:04d}"
    aliases = {
        "jan": 1,
        "feb": 2,
        "mar": 3,
        "apr": 4,
        "may": 5,
        "jun": 6,
        "jul": 7,
        "aug": 8,
        "sep": 9,
        "oct": 10,
        "nov": 11,
        "dec": 12,
    }
    try:
        month_value = int(month_text)
    except ValueError:
        month_value = aliases.get(month_text[:3].casefold(), 0)
    if 1 <= month_value <= 12:
        return f"{year_value:04d}-{month_value:02d}"
    return f"{year_value:04d}"


def _doi_from_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value or "")
    if parsed.netloc.casefold() in {"doi.org", "dx.doi.org"}:
        return normalize_doi(parsed.path.lstrip("/"))
    return ""


def collect_webofscience(
    client: APIClient, config: dict[str, Any], start: datetime, end: datetime
) -> tuple[list[Article], bool, str]:
    """Collect recently added/modified records from Web of Science Starter API."""
    api_key = (
        os.getenv("WOS_API_KEY", "").strip()
        or os.getenv("WEB_OF_SCIENCE_API_KEY", "").strip()
    )
    if not api_key:
        raise SourceUnavailable(
            "Web of Science is configured but WOS_API_KEY is not set"
        )
    source_config = config["sources"]["webofscience"]
    database = str(source_config.get("database", "WOS"))
    page_size = min(50, max(1, int(source_config.get("page_size", 50))))
    maximum = int(source_config.get("max_articles", 200))
    max_pages = max(1, int(source_config.get("max_pages_per_query", 1)))
    queries = list(config.get("wos_queries", []))
    if not queries:
        queries = [f'TS=("{query}")' for query in config.get("retrieval_queries", [])[:5]]
    years = (
        str(start.year)
        if start.year == end.year
        else f"({start.year}-{end.year})"
    )
    articles: list[Article] = []
    seen: set[str] = set()
    partial, last_error = False, ""
    for topic_query in queries:
        for page in range(1, max_pages + 1):
            params = {
                "q": f"({topic_query}) AND PY={years}",
                "db": database,
                "limit": page_size,
                "page": page,
                "sortField": "LD+D",
                "modifiedTimeSpan": (
                    f"{start.date().isoformat()}+{end.date().isoformat()}"
                ),
            }
            try:
                payload = client.get_json(
                    "webofscience",
                    "https://api.clarivate.com/apis/wos-starter/v1/documents",
                    params,
                    headers={"X-ApiKey": api_key},
                )
            except RuntimeError as exc:
                if not articles:
                    raise
                partial, last_error = True, str(exc)
                break
            hits = payload.get("hits", [])
            if not isinstance(hits, list):
                raise RuntimeError("webofscience response has no hits list")
            for item in hits:
                if not isinstance(item, dict):
                    continue
                identifiers = item.get("identifiers") or {}
                source = item.get("source") or {}
                names = item.get("names") or {}
                links = item.get("links") or {}
                keywords = item.get("keywords") or {}
                doi = normalize_doi(str(identifiers.get("doi", "")))
                title = str(item.get("title", ""))
                key = doi or normalize_title(title)
                if not key or key in seen:
                    continue
                seen.add(key)
                authors = []
                for author in names.get("authors", []) or []:
                    if not isinstance(author, dict):
                        continue
                    name = str(
                        author.get("displayName")
                        or author.get("wosStandard")
                        or ""
                    ).strip()
                    if name:
                        authors.append(name)
                record_url = str(links.get("record", ""))
                article_types = item.get("types", []) or item.get("sourceTypes", []) or []
                articles.append(
                    normalize_article(
                        Article(
                            title=title,
                            authors=authors,
                            abstract="",
                            journal=str(source.get("sourceTitle", "")),
                            published_date=_year_month(
                                source.get("publishYear"), source.get("publishMonth")
                            ),
                            doi=doi,
                            url=(
                                f"https://doi.org/{doi}"
                                if doi
                                else record_url
                            ),
                            sources=["webofscience"],
                            publication_type=", ".join(str(x) for x in article_types)
                            or "Web of Science record",
                            keywords=[
                                str(x)
                                for x in keywords.get("authorKeywords", []) or []
                            ],
                            query_ids=planned_query_ids(
                                config, "webofscience", str(topic_query)
                            ),
                            discovery_methods=["keyword", "booster"],
                        )
                    )
                )
                if len(articles) >= maximum:
                    return articles, partial, last_error
            if len(hits) < page_size:
                break
        if partial:
            break
    return articles, partial, last_error


def collect_google_scholar(
    client: APIClient, config: dict[str, Any], start: datetime, end: datetime
) -> tuple[list[Article], bool, str]:
    """Collect Google Scholar results through SerpApi without scraping Scholar directly."""
    api_key = os.getenv("SERPAPI_KEY", "").strip()
    if not api_key:
        raise SourceUnavailable(
            "Google Scholar is configured via SerpApi but SERPAPI_KEY is not set"
        )
    source_config = config["sources"]["google_scholar"]
    page_size = min(20, max(1, int(source_config.get("page_size", 20))))
    maximum = int(source_config.get("max_articles", 100))
    max_pages = max(1, int(source_config.get("max_pages_per_query", 1)))
    language = str(source_config.get("language", "en"))
    queries = list(config.get("google_scholar_queries", []))
    if not queries:
        queries = list(config.get("retrieval_queries", []))[:5]
    articles: list[Article] = []
    seen: set[str] = set()
    partial, last_error = False, ""
    for query in queries:
        for page in range(max_pages):
            params = {
                "engine": "google_scholar",
                "q": query,
                "api_key": api_key,
                "as_ylo": start.year,
                "as_yhi": end.year,
                "scisbd": 2,
                "as_sdt": 0,
                "hl": language,
                "num": page_size,
                "start": page * page_size,
            }
            try:
                payload = client.get_json(
                    "google_scholar", "https://serpapi.com/search.json", params
                )
            except RuntimeError as exc:
                if not articles:
                    raise
                partial, last_error = True, str(exc)
                break
            if payload.get("error"):
                error = f"google_scholar provider error: {payload['error']}"
                if not articles:
                    raise RuntimeError(error)
                partial, last_error = True, error
                break
            results = payload.get("organic_results", [])
            if not isinstance(results, list):
                raise RuntimeError("google_scholar response has no organic_results list")
            for item in results:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title", ""))
                link = str(item.get("link", ""))
                doi = _doi_from_url(link)
                key = doi or normalize_title(title)
                if not key or key in seen:
                    continue
                seen.add(key)
                publication_info = item.get("publication_info") or {}
                summary = str(publication_info.get("summary", ""))
                year_matches = re.findall(r"(?:19|20)\d{2}", summary)
                published_date = year_matches[-1] if year_matches else ""
                authors = [
                    str(author.get("name", "")).strip()
                    for author in publication_info.get("authors", []) or []
                    if isinstance(author, dict) and author.get("name")
                ]
                summary_parts = [part.strip() for part in summary.split(" - ")]
                journal = summary_parts[1] if len(summary_parts) >= 3 else ""
                journal = re.sub(r",?\s*(?:19|20)\d{2}.*$", "", journal).strip()
                articles.append(
                    normalize_article(
                        Article(
                            title=title,
                            authors=authors,
                            abstract="",
                            journal=journal,
                            published_date=published_date,
                            doi=doi,
                            url=link,
                            sources=["google_scholar"],
                            publication_type="Google Scholar result",
                            query_ids=planned_query_ids(
                                config, "google_scholar", str(query)
                            ),
                            discovery_methods=["keyword", "booster"],
                        )
                    )
                )
                if len(articles) >= maximum:
                    return articles, partial, last_error
            if len(results) < page_size:
                break
        if partial:
            break
    return articles, partial, last_error


def merge_articles(left: Article, right: Article) -> Article:
    had_preprint = left.is_preprint or right.is_preprint
    had_published = (not left.is_preprint) or (not right.is_preprint)
    primary, secondary = (right, left) if left.is_preprint and not right.is_preprint else (left, right)
    for name in (
        "title",
        "abstract",
        "journal",
        "published_date",
        "online_date",
        "indexed_date",
        "doi",
        "url",
        "publication_type",
    ):
        if not getattr(primary, name) or (
            name == "abstract" and len(getattr(secondary, name)) > len(getattr(primary, name))
        ):
            setattr(primary, name, getattr(secondary, name))
    primary.authors = list(dict.fromkeys(primary.authors + secondary.authors))
    primary.sources = sorted(set(primary.sources + secondary.sources))
    primary.keywords = list(dict.fromkeys(primary.keywords + secondary.keywords))
    primary.subjects = list(dict.fromkeys(primary.subjects + secondary.subjects))
    primary.query_ids = list(dict.fromkeys(primary.query_ids + secondary.query_ids))
    primary.discovery_methods = list(
        dict.fromkeys(primary.discovery_methods + secondary.discovery_methods)
    )
    primary.citation_seed_ids = list(
        dict.fromkeys(primary.citation_seed_ids + secondary.citation_seed_ids)
    )
    primary.serendipity_bridges = list(
        dict.fromkeys(primary.serendipity_bridges + secondary.serendipity_bridges)
    )
    links = set(primary.related_dois + secondary.related_dois)
    if secondary.doi:
        links.add(secondary.doi)
    if primary.doi:
        links.discard(primary.doi)
    primary.related_dois = sorted(links)
    primary.is_preprint = primary.is_preprint and secondary.is_preprint
    if had_preprint and had_published:
        primary.version_status = "published_with_preprint"
    return normalize_article(primary)


def _article_year(article: Article) -> int | None:
    for value in (article.online_date, article.published_date, article.indexed_date):
        match = re.search(r"(?:19|20)\d{2}", value or "")
        if match:
            return int(match.group(0))
    return None


def _author_surnames(article: Article) -> set[str]:
    return {
        re.sub(r"[^a-z0-9-]", "", name.casefold().split()[-1])
        for name in article.authors
        if name.strip()
    }


def same_physical_article(left: Article, right: Article, threshold: float) -> bool:
    left_links = set(left.related_dois + ([left.doi] if left.doi else []))
    right_links = set(right.related_dois + ([right.doi] if right.doi else []))
    if left_links & right_links:
        return True
    exact_title = left.normalized_title == right.normalized_title
    similarity = 0.0
    if min(len(left.normalized_title), len(right.normalized_title)) >= 30:
        similarity = SequenceMatcher(None, left.normalized_title, right.normalized_title).ratio()
    doi_conflict = bool(left.doi and right.doi and left.doi != right.doi)
    author_left, author_right = _author_surnames(left), _author_surnames(right)
    author_overlap = bool(author_left & author_right)
    years = (_article_year(left), _article_year(right))
    year_compatible = not all(years) or abs(int(years[0]) - int(years[1])) <= 2
    preprint_pair = left.is_preprint != right.is_preprint
    if exact_title:
        return not doi_conflict or author_overlap or preprint_pair
    if similarity < threshold:
        return False
    if doi_conflict and not preprint_pair:
        return False
    if preprint_pair:
        return author_overlap and year_compatible
    return not doi_conflict


def deduplicate(articles: Iterable[Article], threshold: float) -> list[Article]:
    unique: list[Article] = []
    doi_index: dict[str, int] = {}
    for raw in articles:
        article = normalize_article(raw)
        if not article.title:
            continue
        linked = set(article.related_dois + ([article.doi] if article.doi else []))
        matched: int | None = next((doi_index[x] for x in linked if x in doi_index), None)
        if matched is None:
            for index, existing in enumerate(unique):
                if same_physical_article(article, existing, threshold):
                    matched = index
                    break
        if matched is None:
            matched = len(unique)
            unique.append(article)
        else:
            unique[matched] = merge_articles(unique[matched], article)
        for doi in unique[matched].related_dois + ([unique[matched].doi] if unique[matched].doi else []):
            doi_index[doi] = matched
    return unique


def journal_group(venue: str, config: dict[str, Any]) -> str:
    """Match configured full/alias journal names, including index-specific titles."""
    key = normalize_title(venue)
    for journal in config.get("journal_watch", {}).get("journals", []):
        names = [journal["name"]] + journal.get("aliases", [])
        if key in {normalize_title(name) for name in names}:
            return str(journal.get("group", ""))
    return ""


def contains(text: str, keyword: str) -> bool:
    if not keyword.strip():
        return False
    return re.search(
        r"(?<!\w)" + re.escape(keyword.casefold()) + r"(?!\w)", text.casefold()
    ) is not None


def assign_category(topics: set[str], config: dict[str, Any]) -> str:
    """Assign the category of the highest-weight matched topic."""
    configured_topics = config.get("primary_topics", {})
    ranked = sorted(
        (
            (float(configured_topics[name].get("weight", 1.0)), name)
            for name in topics
            if name in configured_topics
        ),
        reverse=True,
    )
    for _, topic_name in ranked:
        category = str(configured_topics[topic_name].get("category", "")).strip()
        if category:
            return category
    return "Broader ecology"


def score_article(article: Article, config: dict[str, Any]) -> Article:
    scoring = config["scoring"]
    raw_score = 0.0
    matched: list[str] = []
    for topic_name, topic in config.get("primary_topics", {}).items():
        title_hits = sum(contains(article.title, keyword) for keyword in topic.get("keywords", []))
        abstract_hits = sum(contains(article.abstract, keyword) for keyword in topic.get("keywords", []))
        if title_hits or abstract_hits:
            matched.append(topic_name)
            raw_score += float(topic.get("weight", 1.0)) * (
                min(title_hits, 3) * float(scoring.get("title_multiplier", 3.0))
                + min(abstract_hits, 5) * float(scoring.get("abstract_multiplier", 1.0))
                + float(scoring.get("topic_presence_bonus", 2.0))
            )
    combined_text = f"{article.title} {article.abstract} {article.journal}"
    secondary_hits = sum(
        contains(combined_text, keyword) for keyword in config.get("secondary_topics", [])
    )
    raw_score += min(secondary_hits, 5) * float(scoring.get("secondary_keyword_points", 2.0))
    if any(contains(combined_text, keyword) for keyword in config.get("domain_keywords", [])):
        raw_score += float(scoring.get("domain_keyword_bonus", 4.0))
    if article.journal.casefold() in {
        journal.casefold() for journal in config.get("target_journals", [])
    }:
        raw_score += float(scoring.get("journal_bonus", 8.0))
    if len(matched) > 1:
        raw_score += (len(matched) - 1) * float(scoring.get("multiple_topic_bonus", 4.0))
    matched_set = set(matched)
    for pair in scoring.get("combinations", []):
        if len(pair) == 2 and set(pair).issubset(matched_set):
            raw_score += float(scoring.get("combination_bonus", 8.0))
    negative_hits = sum(
        contains(combined_text, keyword) for keyword in config.get("negative_keywords", [])
    )
    raw_score = max(
        0.0,
        raw_score - negative_hits * float(scoring.get("negative_keyword_penalty", 10.0)),
    )
    scale = max(1.0, float(scoring.get("max_raw_score_for_100", 45.0)))
    article.rule_score = round(min(100.0, 100.0 * raw_score / scale), 1)
    article.matched_topics = matched
    article.category = assign_category(matched_set, config)
    return article


def find_serendipity_bridges(article: Article, config: dict[str, Any]) -> list[str]:
    combined = " ".join(
        [article.title, article.abstract, article.journal] + article.keywords + article.subjects
    )
    bridges: list[str] = []
    configured = config.get("search", {}).get("serendipity", {}).get("bridges", {})
    for name, terms in configured.items():
        if any(contains(combined, str(term)) for term in terms):
            bridges.append(str(name))
    return bridges


def classify_relevance(article: Article, config: dict[str, Any]) -> Article:
    """Assign a transparent preclassification; Codex remains the final scientific judge."""
    serendipity_ids = set(
        config.get("_search_plan", {}).get("serendipity_query_ids", [])
    )
    article.serendipity_bridges = find_serendipity_bridges(article, config)
    from_serendipity = bool(serendipity_ids.intersection(article.query_ids)) or any(
        "frontier_transfer" in query_id for query_id in article.query_ids
    )
    from_method = any(
        marker in query_id for query_id in article.query_ids for marker in ("-method", "frontier_transfer")
    )
    general_methods = config.get("primary_topics", {}).get("transferable_methods", {}).get("keywords", [])
    transferable = any(contains(f"{article.title} {article.abstract}", term) for term in general_methods)
    if transferable and not article.matched_topics:
        article.relevance_class = "method_transfer"
    elif from_serendipity and article.serendipity_bridges:
        article.relevance_class = "serendipity"
    elif from_method and article.matched_topics:
        article.relevance_class = "method_transfer"
    elif len(article.matched_topics) >= 2 or article.rule_score >= 65:
        article.relevance_class = "direct_hit"
    elif article.matched_topics:
        article.relevance_class = "adjacent_topic"
    elif any(
        contains(f"{article.title} {article.abstract}", keyword)
        for keyword in config.get("domain_keywords", [])
    ):
        article.relevance_class = "broader_ecology"
    else:
        article.relevance_class = "unclassified"
    article.evidence_level = "abstract-supported" if article.abstract else "title-only"
    return article


class HistoryDB:
    def __init__(self, state_dir: Path):
        state_dir.mkdir(parents=True, exist_ok=True)
        self.path = state_dir / "literature.db"
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doi TEXT,
                normalized_title TEXT NOT NULL,
                title TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                reported_date TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_skill_doi
                ON articles(doi) WHERE doi IS NOT NULL AND doi != '';
            CREATE UNIQUE INDEX IF NOT EXISTS idx_skill_title
                ON articles(normalized_title);
            """
        )
        self.connection.commit()

    def _find(self, article: Article) -> sqlite3.Row | None:
        row = None
        identifiers = list(dict.fromkeys(
            value for value in [article.doi, *article.related_dois] if value
        ))
        if identifiers:
            placeholders = ",".join("?" for _ in identifiers)
            row = self.connection.execute(
                f"SELECT * FROM articles WHERE doi IN ({placeholders}) ORDER BY reported_date IS NOT NULL DESC LIMIT 1",
                identifiers,
            ).fetchone()
        if row is None:
            row = self.connection.execute(
                "SELECT * FROM articles WHERE normalized_title=?", (article.normalized_title,)
            ).fetchone()
        return row

    def upsert_seen(self, article: Article) -> None:
        now = datetime.now(timezone.utc).isoformat()
        metadata = json.dumps(article.serializable(), ensure_ascii=False, separators=(",", ":"))
        row = self._find(article)
        if row:
            self.connection.execute(
                "UPDATE articles SET doi=?, normalized_title=?, title=?, metadata_json=?, last_seen=? WHERE id=?",
                (article.doi, article.normalized_title, article.title, metadata, now, row["id"]),
            )
            article.previously_reported = bool(row["reported_date"])
        else:
            self.connection.execute(
                "INSERT INTO articles (doi, normalized_title, title, metadata_json, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
                (article.doi, article.normalized_title, article.title, metadata, now, now),
            )
        self.connection.commit()

    def mark_reported(
        self,
        doi: str,
        title: str,
        report_date: str,
        related_dois: Iterable[str] | None = None,
    ) -> None:
        article = normalize_article(
            Article(title=title, doi=doi, related_dois=list(related_dois or []))
        )
        row = self._find(article)
        now = datetime.now(timezone.utc).isoformat()
        if row:
            self.connection.execute(
                "UPDATE articles SET reported_date=?, last_seen=? WHERE id=?",
                (report_date, now, row["id"]),
            )
        else:
            self.connection.execute(
                "INSERT INTO articles (doi, normalized_title, title, metadata_json, first_seen, last_seen, reported_date) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    article.doi,
                    article.normalized_title,
                    article.title,
                    json.dumps(article.serializable(), ensure_ascii=False),
                    now,
                    now,
                    report_date,
                ),
            )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()


def default_state_dir() -> Path:
    configured = os.getenv("ECOLOGY_BRIEF_HOME", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / "Documents" / "Codex" / "EcologyMorningBrief"
