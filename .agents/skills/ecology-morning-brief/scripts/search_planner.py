#!/usr/bin/env python3
"""Mode-aware, bounded query planning for Ecology Morning Brief v2."""

from __future__ import annotations

import math
import re
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo


VALID_MODES = ("daily", "weekly-deep", "topic-deep")
QUERY_TYPES = ("direct", "mechanism", "method", "application", "review", "frontier_transfer")


def _unique(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = " ".join(str(value).strip().split())
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _bounded(values: Iterable[Any], limit: int) -> list[str]:
    return _unique(values)[: max(0, limit)]


def _phrase(value: str) -> str:
    value = " ".join(value.replace('"', "").split())
    return f'"{value}"' if " " in value else value


def _join_terms(values: Iterable[str], maximum: int) -> tuple[str, list[str]]:
    terms = _bounded(values, maximum)
    return " ".join(_phrase(value) for value in terms), terms


def _module_text(module: dict[str, Any]) -> str:
    fields = ("label", "taxa", "processes", "life_stages", "mechanisms", "risks", "methods", "applications")
    values: list[str] = []
    for field in fields:
        value = module.get(field, [])
        values.extend(value if isinstance(value, list) else [value])
    return " ".join(values).casefold()


def _context_module(profile: dict[str, Any], modules: dict[str, Any]) -> dict[str, Any] | None:
    queries = _unique(profile.get("retrieval_queries", []))
    topics = profile.get("primary_topics", {})
    keywords: list[str] = []
    topic_keys: list[str] = []
    if isinstance(topics, dict):
        for key, topic in topics.items():
            if not isinstance(topic, dict):
                continue
            topic_keys.append(str(key))
            keywords.extend(topic.get("keywords", []))
    keywords = _unique(keywords)
    if not queries and not keywords:
        return None

    context_text = " ".join(
        queries + keywords + [str(profile.get("focus_summary", ""))]
    ).casefold()
    best: dict[str, Any] = {}
    best_score = -1
    for module in modules.values():
        if not isinstance(module, dict):
            continue
        terms = set(re.findall(r"[a-z0-9-]+", _module_text(module)))
        score = sum(1 for term in terms if len(term) > 3 and term in context_text)
        if score > best_score:
            best, best_score = module, score

    return {
        "label": str(profile.get("focus_summary") or "Explicit project focus"),
        "topic_keys": topic_keys,
        "focus_queries": queries,
        "taxa": _bounded(keywords, 3),
        "processes": _bounded(keywords[3:] or keywords, 4),
        "life_stages": list(best.get("life_stages", [])),
        "mechanisms": list(best.get("mechanisms", [])),
        "risks": list(best.get("risks", [])),
        "methods": list(best.get("methods", [])),
        "applications": list(best.get("applications", [])),
        "preferred_sources": list(best.get("preferred_sources", ["openalex", "semantic_scholar", "crossref"])),
        "optional_sources": list(best.get("optional_sources", ["webofscience", "google_scholar"])),
        "explicit_context": True,
    }


def _legacy_modules(config: dict[str, Any], maximum: int) -> dict[str, dict[str, Any]]:
    """Turn a v1 query list into bounded temporary modules instead of failing."""
    preferred = [
        source
        for source in ("openalex", "semantic_scholar", "crossref", "europepmc", "biorxiv")
        if config.get("sources", {}).get(source, {}).get("enabled", False)
    ]
    optional = [
        source
        for source in ("webofscience", "google_scholar")
        if config.get("sources", {}).get(source, {}).get("enabled", False)
    ]
    modules: dict[str, dict[str, Any]] = {}
    for index, query in enumerate(_unique(config.get("retrieval_queries", []))[:maximum], start=1):
        terms = _unique(left or right for left, right in re.findall(r'"([^"]+)"|([^\s]+)', query))
        modules[f"legacy_{index:02d}"] = {
            "label": f"Legacy query {index}",
            "focus_queries": [query],
            "taxa": terms[:1],
            "processes": terms[1:3] or terms[:2],
            "life_stages": terms[3:4],
            "mechanisms": terms[4:6],
            "risks": terms[6:7],
            "methods": terms[2:4],
            "applications": terms[-2:],
            "preferred_sources": preferred,
            "optional_sources": optional,
        }
    return modules


def _query_terms(query_type: str, module: dict[str, Any]) -> list[str]:
    explicit = module.get("queries", {}).get(query_type)
    if explicit:
        return [str(explicit)]
    taxa = list(module.get("taxa", []))
    processes = list(module.get("processes", []))
    stages = list(module.get("life_stages", []))
    mechanisms = list(module.get("mechanisms", []))
    risks = list(module.get("risks", []))
    methods = list(module.get("methods", []))
    applications = list(module.get("applications", []))
    focus_queries = list(module.get("focus_queries", []))
    if query_type == "direct":
        if focus_queries:
            return [focus_queries[0]]
        return processes[:1] + taxa[:1]
    if query_type == "mechanism":
        return processes[1:2] + (mechanisms[:1] or taxa[:1])
    if query_type == "method":
        return methods[:1] + ["ecology"]
    if query_type == "application":
        return applications[:1] + (risks[:1] or taxa[:1])
    if query_type == "review":
        return processes[:1] + ["review"]
    return methods[1:2] + (applications[:1] or ["ecology"])


def _rationale(query_type: str) -> str:
    return {
        "direct": "Maximize precision for the module's focal taxon, process, stage, and risk.",
        "mechanism": "Recover mechanistic and life-stage work that may omit the main application phrase.",
        "method": "Find reusable modelling or analytical approaches across systems and disciplines.",
        "application": "Connect mechanisms and risks to conservation, management, or forecasting outcomes.",
        "review": "Recover synthesis papers that can serve as bounded citation-network seeds.",
        "frontier_transfer": "Explore methods or theory with an explicit scientific idea or plausible transfer route.",
    }[query_type]


def _query_weight(query_type: str) -> float:
    return {"direct": 1.0, "mechanism": 0.92, "method": 0.78, "application": 0.82, "review": 0.74, "frontier_transfer": 0.55}[query_type]


def _window(report_date: str | None, lookback: int, timezone_name: str = "Asia/Shanghai") -> tuple[date, datetime, datetime]:
    local_zone = ZoneInfo(timezone_name)
    now = datetime.now(local_zone)
    if report_date:
        report_day = date.fromisoformat(report_date)
        end = datetime.combine(report_day, datetime_time.max, tzinfo=local_zone)
        if report_day == now.date():
            end = now
    else:
        end = now
        report_day = now.date()
    return report_day, end - timedelta(hours=lookback), end


def build_search_plan(
    config: dict[str, Any],
    profile: dict[str, Any] | None = None,
    *,
    mode: str | None = None,
    report_date: str | None = None,
    lookback: int | None = None,
) -> dict[str, Any]:
    """Build a small set of orthogonal, auditable queries without a Cartesian product."""
    search = config.get("search", {})
    selected_mode = str(mode or search.get("mode", "daily")).strip().casefold()
    if selected_mode not in VALID_MODES:
        raise ValueError(f"mode must be one of: {', '.join(VALID_MODES)}")
    mode_config = dict(search.get("modes", {}).get(selected_mode, {}))
    actual_lookback = int(lookback or mode_config.get("lookback_hours") or config.get("lookback_hours", 48))
    report_day, start, end = _window(report_date, actual_lookback, str(config.get("report_timezone", "Asia/Shanghai")))
    max_terms = int(search.get("query_expansion", {}).get("max_terms_per_concept", 4))
    max_blocks = int(search.get("query_expansion", {}).get("max_concept_blocks", 4))
    max_terms_total = max(2, max_terms + max_blocks - 1)
    max_modules = max(1, int(mode_config.get("max_modules", 5)))
    per_module = max(1, int(mode_config.get("max_queries_per_module", 3)))
    allowed_types = [value for value in search.get("query_expansion", {}).get("query_types", QUERY_TYPES) if value in QUERY_TYPES]
    mode_types = allowed_types[:per_module]

    configured_modules = {
        str(key): value
        for key, value in dict(search.get("modules", {})).items()
        if isinstance(value, dict)
    }
    if not configured_modules:
        configured_modules = _legacy_modules(config, max_modules)
    active: list[tuple[str, dict[str, Any]]] = []
    profile = profile or {}
    context = _context_module(profile, configured_modules)
    retrieval_mode = str(profile.get("retrieval_mode", "extend")).casefold()
    context_applied = bool(profile.get("context_applied", bool(context)))
    dynamic = profile.get("context_modules", config.get("_context_modules", {}))
    if context_applied and dynamic and retrieval_mode == "extend":
        # Reserve persistent broad modules; dynamic context uses only remaining slots.
        available = max(0, max_modules - len(configured_modules))
        cap = min(available, int(config.get("context_refresh", {}).get("max_context_modules", 4)))
        for key, module in list(dynamic.items())[:cap]:
            if isinstance(module, dict) and module.get("queries", {}).get("direct"):
                active.append(("context_" + str(key), module))
    elif context_applied and context:
        active.append(("context_focus", context))
    if retrieval_mode != "focus":
        active.extend(configured_modules.items())
    if not active:
        raise ValueError("No search modules or explicit context queries are configured")
    active = active[:max_modules]

    citation_allowed = bool(search.get("citation_expansion", {}).get(selected_mode, False))
    queries: list[dict[str, Any]] = []
    for type_index, query_type in enumerate(mode_types, start=1):
        for module_index, (module_key, module) in enumerate(active, start=1):
            if module.get("context_interest") and query_type not in module.get("queries", {}):
                continue
            terms = _query_terms(query_type, module)
            query, used_terms = _join_terms(terms, max_terms_total)
            if module.get("queries", {}).get(query_type):
                query = str(module["queries"][query_type])
            elif query_type == "direct" and module.get("focus_queries"):
                # A supplied search expression is already a query, not one giant exact phrase.
                query = str(module["focus_queries"][0])
            if not query:
                continue
            query_id = f"q{module_index:02d}-{type_index:02d}-{module_key}-{query_type}"
            queries.append(
                {
                    "query_id": query_id,
                    "module": module_key,
                    "module_label": str(module.get("label", module_key)),
                    "query_type": query_type,
                    "query": query,
                    "terms": used_terms,
                    "rationale": _rationale(query_type),
                    "preferred_sources": _unique(module.get("preferred_sources", [])),
                    "optional_sources": _unique(module.get("optional_sources", [])),
                    "weight": _query_weight(query_type),
                    "citation_expansion_allowed": citation_allowed and query_type in {"direct", "mechanism", "method", "review"},
                    "serendipity": query_type == "frontier_transfer",
                }
            )

    serendipity_fraction = min(0.4, max(0.0, float(search.get("serendipity_fraction", 0.15))))
    if serendipity_fraction and not any(query["serendipity"] for query in queries):
        source_module_key, source_module = next(
            ((key, value) for key, value in active if value.get("methods")), active[0]
        )
        query, used_terms = _join_terms(_query_terms("frontier_transfer", source_module), max_terms_total)
        if query:
            queries.append(
                {
                    "query_id": f"qx01-{source_module_key}-frontier_transfer",
                    "module": source_module_key,
                    "module_label": str(source_module.get("label", source_module_key)),
                    "query_type": "frontier_transfer",
                    "query": query,
                    "terms": used_terms,
                    "rationale": _rationale("frontier_transfer"),
                    "preferred_sources": _unique(source_module.get("preferred_sources", ["openalex", "semantic_scholar"])),
                    "optional_sources": _unique(source_module.get("optional_sources", [])),
                    "weight": _query_weight("frontier_transfer"),
                    "citation_expansion_allowed": False,
                    "serendipity": True,
                }
            )

    max_planned = max_modules * per_module + max(1, math.ceil(max_modules * per_module * serendipity_fraction))
    queries = queries[:max_planned]
    return {
        "schema_version": 2,
        "report_date": report_day.isoformat(),
        "mode": selected_mode,
        "window": {"start": start.isoformat(), "end": end.isoformat(), "lookback_hours": actual_lookback},
        "context": {
            "applied": context_applied,
            "retrieval_mode": retrieval_mode,
            "focus_summary": str(profile.get("focus_summary", config.get("_context", {}).get("focus_summary", ""))).strip(),
            "refresh": profile.get("_context_refresh", config.get("_context", {}).get("refresh", {})),
        },
        "limits": {
            "max_modules": max_modules,
            "max_queries_per_module": per_module,
            "max_seed_papers": int(mode_config.get("max_seed_papers", 0)),
            "max_citations_per_seed": int(mode_config.get("max_citations_per_seed", 0)),
            "max_hops": int(search.get("citation_expansion", {}).get("max_hops", 1)),
        },
        "journal_watch_enabled": (selected_mode in config.get("journal_watch", {}).get("enabled_modes", []) and retrieval_mode != "focus"),
        "serendipity_fraction": serendipity_fraction,
        "modules": [key for key, _ in active],
        "queries": queries,
    }
