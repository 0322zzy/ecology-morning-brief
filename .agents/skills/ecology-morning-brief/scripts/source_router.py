#!/usr/bin/env python3
"""Route planned queries to complementary core and booster sources."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from brief_engine import load_config


SOURCE_QUERY_FIELDS = {
    "crossref": "retrieval_queries",
    "openalex": "openalex_queries",
    "semantic_scholar": "semantic_scholar_queries",
    "webofscience": "wos_queries",
    "google_scholar": "google_scholar_queries",
    "arxiv": "arxiv_queries",
    "europepmc": "europepmc_queries",
}


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = " ".join(str(value).split())
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _wos_query(value: str) -> str:
    if value.strip().upper().startswith(("TS=", "TI=", "AU=", "SO=", "DO=")):
        return value
    return f"TS=({value})"


def _select_query_ids(
    query_ids: list[str], query_by_id: dict[str, dict[str, Any]], limit: int
) -> list[str]:
    """Keep a bounded mix of direct, mechanism, method, and exploration queries."""
    ordered = _unique(query_ids)
    chosen: list[str] = []

    def take(predicate) -> None:
        for query_id in ordered:
            if query_id not in chosen and predicate(query_by_id.get(query_id, {})):
                chosen.append(query_id)
                return

    take(lambda query: query.get("query_type") == "direct" and query.get("module") == "context_focus")
    for query_type in ("mechanism", "method", "review", "frontier_transfer", "application"):
        take(lambda query, query_type=query_type: query.get("query_type") == query_type)
    take(lambda query: query.get("query_type") == "direct" and query.get("module") != "context_focus")
    # Cover each module before giving the first module more variants.
    covered = {query_by_id.get(query_id, {}).get("module") for query_id in chosen[:limit]}
    diversified = chosen[:min(3, limit)]
    covered = {query_by_id.get(query_id, {}).get("module") for query_id in diversified}
    for query_id in ordered:
        module = query_by_id.get(query_id, {}).get("module")
        if module not in covered and len(diversified) < limit:
            diversified.append(query_id)
            covered.add(module)
    chosen = diversified + [query_id for query_id in chosen if query_id not in diversified]
    chosen.extend(query_id for query_id in ordered if query_id not in chosen)
    return chosen[:limit]


def route_search_plan(plan: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    mode = str(plan.get("mode", "daily"))
    queries = list(plan.get("queries", []))
    query_by_id = {str(query.get("query_id", "")): query for query in queries}
    source_config = dict(config.get("sources", {}))
    source_limits = config.get("search", {}).get("modes", {}).get(mode, {}).get("source_query_limits", {})
    routes: dict[str, dict[str, Any]] = {}
    for source, settings in source_config.items():
        source_class = str(settings.get("class", "core"))
        routes[source] = {
            "class": source_class,
            "enabled": bool(settings.get("enabled", False)),
            "query_ids": [],
            "queries": [],
            "reason": "",
        }

    for query in queries:
        preferred = list(query.get("preferred_sources", []))
        optional = list(query.get("optional_sources", []))
        query_type = str(query.get("query_type", "direct"))
        if query_type == "frontier_transfer" and mode != "daily" and "arxiv" in source_config:
            preferred.append("arxiv")
        for source in _unique(preferred + optional):
            if source not in routes or not routes[source]["enabled"]:
                continue
            if source == "arxiv" and query_type not in {"method", "frontier_transfer"}:
                continue
            routes[source]["query_ids"].append(str(query.get("query_id", "")))
            routes[source]["queries"].append(str(query.get("query", "")))

    always_scan = {"biorxiv", "europepmc"}
    for source, route in routes.items():
        route["query_ids"] = _unique(route["query_ids"])
        limit = max(1, int(source_limits.get(source, len(route["query_ids"]) or 1)))
        route["query_ids"] = _select_query_ids(route["query_ids"], query_by_id, limit)
        route["queries"] = [
            str(query_by_id[query_id].get("query", ""))
            for query_id in route["query_ids"] if query_id in query_by_id
        ]
        route["query_limit"] = limit
        if source == "journal_watch":
            route["active_core"] = (route["enabled"] and
                mode in config.get("journal_watch", {}).get("enabled_modes", ["daily", "weekly-deep"]) and
                plan.get("context", {}).get("retrieval_mode") != "focus")
        elif source in always_scan and route["enabled"]:
            route["active_core"] = source in {
                item for query in queries for item in query.get("preferred_sources", [])
            } or (source == "biorxiv" and route["class"] == "supplement")
        else:
            route["active_core"] = bool(route["queries"]) and route["class"] in {"core", "supplement"}
        route["active_booster"] = bool(route["queries"]) and route["class"] == "booster"
        if not route["enabled"]:
            route["reason"] = "disabled in persistent configuration"
        elif route["active_core"]:
            route["reason"] = "preferred by at least one planned query"
        elif route["active_booster"]:
            route["reason"] = "optional booster for at least one planned query"
        else:
            route["reason"] = "not selected by this plan"
    return {"schema_version": 1, "mode": mode, "sources": routes}


def apply_route_to_config(
    config: dict[str, Any], plan: dict[str, Any], route: dict[str, Any]
) -> dict[str, Any]:
    """Return a run-scoped config with source query lists and provenance lookup."""
    effective = copy.deepcopy(config)
    query_by_id = {str(item.get("query_id")): item for item in plan.get("queries", [])}
    lookup: dict[str, dict[str, list[str]]] = {}
    for source, details in route.get("sources", {}).items():
        routed_queries = [
            query_by_id[query_id]
            for query_id in details.get("query_ids", [])
            if query_id in query_by_id
        ]
        values = [str(item.get("query", "")) for item in routed_queries]
        if source == "webofscience":
            values = [_wos_query(value) for value in values]
        field = SOURCE_QUERY_FIELDS.get(source)
        if field:
            effective[field] = _unique(values)
        if source in effective.get("sources", {}) and details.get("query_limit"):
            effective["sources"][source]["max_queries"] = int(details["query_limit"])
        lookup[source] = {}
        for item, adapted in zip(routed_queries, values):
            lookup[source].setdefault(adapted.casefold(), []).append(str(item.get("query_id", "")))
        if source in {"biorxiv", "europepmc"}:
            # These APIs return one category/query scan rather than per-plan-query hits.
            # A source scan must not inherit every query ID (especially serendipity IDs),
            # or contribution and relevance classes become falsely inflated.
            lookup[source]["__source_scan__"] = [f"scan-{source}"]

    # Europe PMC is most useful as a stable domain scan. Keep the persistent
    # high-recall query and extend it only with the run's explicit/context
    # focus. Rebuilding it from every planner term makes generic words such as
    # "population" dominate the result cap and can reduce relevant recall.
    europe_terms: list[str] = []
    for query in plan.get("queries", []):
        if (
            query.get("module") == "context_focus"
            and query.get("query_type") == "direct"
            and "europepmc" in query.get("preferred_sources", []) + query.get("optional_sources", [])
        ):
            europe_terms.extend(query.get("terms", []))
    if europe_terms:
        values = [f'"{value}"' if " " in value else value for value in _unique(europe_terms)[:8]]
        focus_query = "(" + " OR ".join(values) + ")"
        baseline_query = str(config.get("europepmc_query", "")).strip()
        effective["europepmc_query"] = (
            f"({baseline_query} OR {focus_query})" if baseline_query else focus_query
        )
    effective["_search_plan"] = {
        "mode": plan.get("mode", "daily"),
        "query_lookup": lookup,
        "query_order": [str(item.get("query_id", "")) for item in plan.get("queries", [])],
        "serendipity_query_ids": [
            str(item.get("query_id", "")) for item in plan.get("queries", []) if item.get("serendipity")
        ],
    }
    return effective


def query_ids_for(config: dict[str, Any], source: str, query: str = "__source_scan__") -> list[str]:
    lookup = config.get("_search_plan", {}).get("query_lookup", {}).get(source, {})
    return list(lookup.get(query.casefold(), lookup.get("__source_scan__", [])))


def main() -> int:
    parser = argparse.ArgumentParser(description="Route an ecology search plan to sources")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = load_config(args.plan)
    config = load_config(args.config)
    route = route_search_plan(plan, config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(route, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    active = [name for name, value in route["sources"].items() if value.get("active_core") or value.get("active_booster")]
    print(f"wrote={args.output} active_sources={len(active)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
