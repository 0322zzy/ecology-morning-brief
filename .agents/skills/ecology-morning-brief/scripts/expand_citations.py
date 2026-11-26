#!/usr/bin/env python3
"""Expand high-relevance seeds by one citation-network hop and re-screen the union."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from brief_engine import (
    APIClient,
    HistoryDB,
    classify_relevance,
    deduplicate,
    default_state_dir,
    load_config,
    score_article,
)
from citation_network import (
    article_from_dict,
    choose_seeds,
    expand_openalex,
    expand_semantic_scholar,
)


SKILL_DIR = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="One-hop ecology citation expansion")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=SKILL_DIR / "config" / "interests.yaml")
    parser.add_argument("--state-dir", type=Path, default=default_state_dir())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = load_config(args.input)
    config = load_config(args.config)
    mode = str(payload.get("mode", "daily"))
    mode_settings = config.get("search", {}).get("modes", {}).get(mode, {})
    enabled = bool(config.get("search", {}).get("citation_expansion", {}).get(mode, False))
    maximum_seeds = int(payload.get("search_plan", {}).get("limits", {}).get("max_seed_papers", mode_settings.get("max_seed_papers", 0)))
    maximum_per_seed = int(payload.get("search_plan", {}).get("limits", {}).get("max_citations_per_seed", mode_settings.get("max_citations_per_seed", 0)))
    if not enabled or maximum_seeds <= 0 or maximum_per_seed <= 0:
        payload["citation_expansion"] = {
            "enabled": False,
            "reason": f"citation expansion is disabled for {mode}",
            "seeds": [],
            "sources": {},
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote={args.output} citation_expansion=disabled")
        return 0

    original = [article_from_dict(value) for value in payload.get("candidates", [])]
    allowed_query_ids = {
        str(query.get("query_id", ""))
        for query in payload.get("search_plan", {}).get("queries", [])
        if query.get("citation_expansion_allowed")
    }
    seeds = choose_seeds(original, allowed_query_ids, maximum_seeds)
    client = APIClient(config)
    expanded = []
    statuses = {}
    configured_sources = config.get("search", {}).get("citation_expansion", {}).get("sources", [])
    if "openalex" in configured_sources:
        articles, statuses["openalex"] = expand_openalex(client, seeds, maximum_per_seed)
        expanded.extend(articles)
    if "semantic_scholar" in configured_sources:
        articles, statuses["semantic_scholar"] = expand_semantic_scholar(client, seeds, maximum_per_seed)
        expanded.extend(articles)

    union = deduplicate(original + expanded, float(config.get("title_similarity_threshold", 0.94)))
    ranked = sorted(
        (classify_relevance(score_article(article, config), config) for article in union),
        key=lambda article: article.rule_score,
        reverse=True,
    )
    database = HistoryDB(args.state_dir)
    try:
        for article in ranked:
            database.upsert_seen(article)
    finally:
        database.close()
    threshold = float(config.get("candidate_score_threshold", 20))
    limit = int(config.get("candidate_limit", 80))
    candidates = [
        article for article in ranked
        if not article.previously_reported
        and (
            article.rule_score >= threshold
            or (article.discovery_methods and any(value.startswith("citation_") for value in article.discovery_methods) and article.rule_score >= threshold * 0.8)
        )
    ][:limit]
    for direction in ("backward", "forward"):
        if not any(query.get("query_id") == f"cx-{direction}" for query in payload.get("search_plan", {}).get("queries", [])):
            payload.setdefault("search_plan", {}).setdefault("queries", []).append(
                {
                    "query_id": f"cx-{direction}",
                    "module": "citation_network",
                    "module_label": "Citation network",
                    "query_type": f"citation_{direction}",
                    "query": f"one-hop {direction} citations",
                    "terms": [],
                    "rationale": "Recover papers connected to high-relevance seeds.",
                    "preferred_sources": configured_sources,
                    "optional_sources": [],
                    "weight": 0.7,
                    "citation_expansion_allowed": False,
                    "serendipity": False,
                }
            )
    existing_records = {
        str(record.get("key", "")): record
        for record in payload.get("retrieval_audit", {}).get("records", [])
    }
    for article in ranked:
        key = article.doi or article.normalized_title
        existing_records[key] = {
            "key": key,
            "doi": article.doi,
            "title": article.title,
            "sources": article.sources,
            "query_ids": article.query_ids,
            "rule_score": article.rule_score,
            "relevance_class": article.relevance_class,
            "relevant": article.rule_score >= threshold,
            "candidate": not article.previously_reported,
        }
    query_counts = Counter(
        query_id for article in expanded for query_id in article.query_ids
    )
    payload.setdefault("retrieval_audit", {}).setdefault("raw_query_counts", {}).update(query_counts)
    payload["retrieval_audit"]["records"] = list(existing_records.values())
    payload["citation_expansion"] = {
        "enabled": True,
        "hop_limit": 1,
        "seeds": [seed.doi or seed.normalized_title for seed in seeds],
        "sources": statuses,
        "retrieved": len(expanded),
        "unique_union": len(union),
        "retained_after_rescreen": sum(article in candidates for article in ranked if article.discovery_methods),
    }
    payload.setdefault("counts", {})["citation_retrieved"] = len(expanded)
    payload["counts"]["deduplicated"] = len(union)
    payload["counts"]["candidate"] = len(candidates)
    payload["candidates"] = [article.serializable() for article in candidates]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"wrote={args.output} seeds={len(seeds)} citation_records={len(expanded)} "
        f"candidate={len(candidates)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
