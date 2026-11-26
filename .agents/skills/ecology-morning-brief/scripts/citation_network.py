#!/usr/bin/env python3
"""Bounded, structured citation expansion for high-relevance seed papers."""

from __future__ import annotations

import os
import urllib.parse
from dataclasses import fields
from typing import Any

from brief_engine import (
    APIClient,
    Article,
    _openalex_abstract,
    normalize_article,
    normalize_doi,
)


ARTICLE_FIELDS = {value.name for value in fields(Article)}


def article_from_dict(value: dict[str, Any]) -> Article:
    return normalize_article(
        Article(**{key: raw for key, raw in value.items() if key in ARTICLE_FIELDS})
    )


def openalex_article(
    item: dict[str, Any], *, direction: str, seed_key: str
) -> Article:
    primary_location = item.get("primary_location") or {}
    source = primary_location.get("source") or {}
    doi = normalize_doi(str(item.get("doi", "")))
    return normalize_article(
        Article(
            title=str(item.get("title") or item.get("display_name") or ""),
            authors=[
                str((entry.get("author") or {}).get("display_name", ""))
                for entry in item.get("authorships", []) or []
                if isinstance(entry, dict)
            ],
            abstract=_openalex_abstract(item.get("abstract_inverted_index")),
            journal=str(source.get("display_name", "")),
            published_date=str(item.get("publication_date", "")),
            online_date=str(item.get("publication_date", "")),
            doi=doi,
            url=f"https://doi.org/{doi}" if doi else str(primary_location.get("landing_page_url") or item.get("id") or ""),
            sources=["openalex"],
            publication_type=str(item.get("type", "article")),
            subjects=[
                str(topic.get("display_name", ""))
                for topic in item.get("topics", []) or []
                if isinstance(topic, dict)
            ],
            is_preprint=str(item.get("type", "")).casefold() == "preprint",
            query_ids=[f"cx-{direction}"],
            discovery_methods=[f"citation_{direction}"],
            citation_seed_ids=[seed_key],
        )
    )


def semantic_article(
    item: dict[str, Any], *, direction: str, seed_key: str
) -> Article:
    external_ids = item.get("externalIds") or {}
    publication_types = [str(value) for value in item.get("publicationTypes", []) or []]
    return normalize_article(
        Article(
            title=str(item.get("title", "")),
            authors=[
                str(author.get("name", ""))
                for author in item.get("authors", []) or []
                if isinstance(author, dict)
            ],
            abstract=str(item.get("abstract") or ""),
            journal=str(item.get("venue") or ""),
            published_date=str(item.get("publicationDate") or item.get("year") or ""),
            online_date=str(item.get("publicationDate") or ""),
            doi=normalize_doi(str(external_ids.get("DOI", ""))),
            url=str(item.get("url") or ""),
            sources=["semantic_scholar"],
            publication_type=", ".join(publication_types) or "article",
            subjects=[str(value) for value in item.get("fieldsOfStudy", []) or []],
            is_preprint=any("preprint" in value.casefold() for value in publication_types),
            query_ids=[f"cx-{direction}"],
            discovery_methods=[f"citation_{direction}"],
            citation_seed_ids=[seed_key],
        )
    )


def expand_openalex(
    client: APIClient, seeds: list[Article], maximum_per_seed: int
) -> tuple[list[Article], dict[str, Any]]:
    articles: list[Article] = []
    status: dict[str, Any] = {"status": "ok", "count": 0, "errors": []}
    api_key = os.getenv("OPENALEX_API_KEY", "").strip()
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
    per_direction = max(1, maximum_per_seed // 2)
    for seed in seeds:
        seed_key = seed.doi or seed.normalized_title
        try:
            exact = client.get_json(
                "openalex_citations",
                "https://api.openalex.org/works",
                {"filter": f"doi:{seed.doi}", "per_page": 1},
                headers=headers,
            ).get("results", [])
            if not exact:
                continue
            work = exact[0]
            openalex_id = str(work.get("id", "")).rstrip("/").rsplit("/", 1)[-1]
            referenced = [
                str(value).rstrip("/").rsplit("/", 1)[-1]
                for value in work.get("referenced_works", []) or []
            ][:per_direction]
            if referenced:
                backward = client.get_json(
                    "openalex_citations",
                    "https://api.openalex.org/works",
                    {"filter": "openalex_id:" + "|".join(referenced), "per_page": per_direction},
                    headers=headers,
                ).get("results", [])
                articles.extend(
                    openalex_article(item, direction="backward", seed_key=seed_key)
                    for item in backward if isinstance(item, dict)
                )
            if openalex_id:
                forward = client.get_json(
                    "openalex_citations",
                    "https://api.openalex.org/works",
                    {"filter": f"cites:{openalex_id}", "per_page": per_direction, "sort": "publication_date:desc"},
                    headers=headers,
                ).get("results", [])
                articles.extend(
                    openalex_article(item, direction="forward", seed_key=seed_key)
                    for item in forward if isinstance(item, dict)
                )
        except Exception as exc:
            status["errors"].append(str(exc))
    status["count"] = len(articles)
    if status["errors"]:
        status["status"] = "partial" if articles else "failed"
    return articles, status


def expand_semantic_scholar(
    client: APIClient, seeds: list[Article], maximum_per_seed: int
) -> tuple[list[Article], dict[str, Any]]:
    articles: list[Article] = []
    status: dict[str, Any] = {"status": "ok", "count": 0, "errors": []}
    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip() or os.getenv("S2_API_KEY", "").strip()
    headers = {"x-api-key": api_key} if api_key else None
    field_list = (
        "title,abstract,authors,venue,publicationDate,year,externalIds,url,publicationTypes,fieldsOfStudy"
    )
    per_direction = max(1, maximum_per_seed // 2)
    for seed in seeds:
        seed_key = seed.doi or seed.normalized_title
        encoded_id = urllib.parse.quote(f"DOI:{seed.doi}", safe=":")
        for direction, endpoint in (("backward", "references"), ("forward", "citations")):
            try:
                payload = client.get_json(
                    "semantic_scholar_citations",
                    f"https://api.semanticscholar.org/graph/v1/paper/{encoded_id}/{endpoint}",
                    {"fields": field_list, "limit": per_direction},
                    headers=headers,
                )
                for row in payload.get("data", []) or []:
                    item = row.get("citedPaper" if direction == "backward" else "citingPaper") or row
                    if isinstance(item, dict):
                        articles.append(
                            semantic_article(item, direction=direction, seed_key=seed_key)
                        )
            except Exception as exc:
                status["errors"].append(str(exc))
    status["count"] = len(articles)
    if status["errors"]:
        status["status"] = "partial" if articles else "failed"
    return articles, status


def choose_seeds(
    candidates: list[Article], allowed_query_ids: set[str], maximum: int
) -> list[Article]:
    eligible = [
        article for article in candidates
        if article.doi
        and article.relevance_class in {"direct_hit", "adjacent_topic", "method_transfer"}
        and (not allowed_query_ids or allowed_query_ids.intersection(article.query_ids))
    ]
    return sorted(
        eligible,
        key=lambda article: (
            "review" in article.publication_type.casefold(),
            article.rule_score,
            bool(article.abstract),
        ),
        reverse=True,
    )[:maximum]
