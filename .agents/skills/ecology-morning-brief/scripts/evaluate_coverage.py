#!/usr/bin/env python3
"""Compute an explainable operational coverage audit from a v2 retrieval payload."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from brief_engine import load_config, normalize_doi, normalize_title


SKILL_DIR = Path(__file__).resolve().parents[1]


def _record_key(value: dict[str, Any]) -> str:
    return normalize_doi(str(value.get("doi", ""))) or normalize_title(str(value.get("title", "")))


def _selected_keys(selected: dict[str, Any] | None) -> set[str]:
    if not selected:
        return set()
    values = selected.get("articles", selected.get("candidates", []))
    return {_record_key(value) for value in values if isinstance(value, dict) and _record_key(value)}


def build_coverage_report(
    payload: dict[str, Any],
    config: dict[str, Any],
    selected: dict[str, Any] | None = None,
) -> dict[str, Any]:
    audit = payload.get("retrieval_audit", {})
    records = [value for value in audit.get("records", []) if isinstance(value, dict)]
    plan = payload.get("search_plan", {})
    queries = [value for value in plan.get("queries", []) if isinstance(value, dict)]
    selected_keys = _selected_keys(selected)
    raw_query_counts = audit.get("raw_query_counts", {})
    attempts = audit.get("query_attempts", [])
    attempted_ids = {query_id for attempt in attempts for query_id in attempt.get("query_ids", [])}
    successful_ids = {query_id for attempt in attempts if attempt.get("status") == "ok" for query_id in attempt.get("query_ids", [])}
    existing_ids = {str(query.get("query_id", "")) for query in queries}
    for query_id in sorted(attempted_ids - existing_ids):
        queries.append({"query_id":query_id,"module":"journal_watch" if query_id.startswith("journal-") else "source_scan","query_type":"journal_scan" if query_id.startswith("journal-") else "source_scan"})
    query_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    seen_relevant: set[str] = set()
    for query in queries:
        query_id = str(query.get("query_id", ""))
        matches = [record for record in records if query_id in record.get("query_ids", [])]
        keys = {_record_key(record) for record in matches if _record_key(record)}
        relevant_keys = {
            _record_key(record) for record in matches
            if record.get("relevant") and _record_key(record)
        }
        marginal_unique = keys - seen
        marginal_relevant = relevant_keys - seen_relevant
        query_rows.append(
            {
                "query_id": query_id,
                "module": query.get("module"),
                "query_type": query.get("query_type"),
                "attempted": query_id in attempted_ids or bool(raw_query_counts.get(query_id, 0)),
                "successful": query_id in successful_ids or bool(raw_query_counts.get(query_id, 0)),
                "searched": int(raw_query_counts.get(query_id, 0)),
                "unique": len(keys),
                "relevant": len(relevant_keys),
                "selected": len(keys & selected_keys),
                "marginal_unique_gain": len(marginal_unique),
                "marginal_relevant_gain": len(marginal_relevant),
            }
        )
        seen.update(keys)
        seen_relevant.update(relevant_keys)

    source_rows: list[dict[str, Any]] = []
    statuses = payload.get("sources", {})
    for source, status in statuses.items():
        matches = [record for record in records if source in record.get("sources", [])]
        keys = {_record_key(record) for record in matches if _record_key(record)}
        unique_only = {
            _record_key(record) for record in matches
            if len(record.get("sources", [])) == 1 and _record_key(record)
        }
        relevant = {
            _record_key(record) for record in matches
            if record.get("relevant") and _record_key(record)
        }
        source_rows.append(
            {
                "source": source,
                "status": status.get("status"),
                "returned": int(status.get("count", 0)),
                "unique_contribution": len(unique_only),
                "overlap": len(keys - unique_only),
                "relevant_contribution": len(relevant),
                "selected_contribution": len(keys & selected_keys),
            }
        )

    coverage = config.get("search", {}).get("coverage", {})
    threshold = float(coverage.get("marginal_gain_threshold", 0.08))
    streak_target = max(1, int(coverage.get("low_gain_streak", 2)))
    minimum_relevant = max(1, int(coverage.get("minimum_relevant_for_high", 12)))
    relevant_total = sum(1 for record in records if record.get("relevant"))
    low_streak = 0
    attempted_rows = [row for row in query_rows if row["successful"]]
    for row in reversed(attempted_rows):
        denominator = max(1, int(row["unique"]))
        ratio = int(row["marginal_relevant_gain"]) / denominator
        if ratio < threshold:
            low_streak += 1
        else:
            break
    if relevant_total >= minimum_relevant and low_streak >= streak_target:
        level = "high"
        reason = f"the last {low_streak} query expansions added little new relevant material"
    elif relevant_total >= max(3, minimum_relevant // 2) and low_streak >= 1:
        level = "moderate"
        reason = "some diminishing marginal yield is visible, but useful gaps may remain"
    else:
        level = "low"
        reason = "relevant yield is still small or recent queries continue to add material"

    # Diminishing returns under outages/caps are not evidence of high coverage.
    incomplete = [source for source, status in statuses.items() if status.get("status") in {"failed", "unavailable", "partial"}]
    routed_core = {query_id for route in payload.get("source_route", {}).get("sources", {}).values() if route.get("active_core") for query_id in route.get("query_ids", [])}
    unattempted_core = routed_core - attempted_ids if attempts else set()
    if incomplete or unattempted_core:
        if level == "high":
            level = "moderate"
        reason += "; coverage limited by source failures, record/page caps or unattempted core queries"

    active_sources = sum(
        status.get("status") in {"ok", "partial"} for status in statuses.values()
    )
    available_sources = sum(
        status.get("status") not in {"disabled", "not_routed", "deferred"}
        for status in statuses.values()
    )
    return {
        "schema_version": 1,
        "label": "operational search saturation",
        "context_refresh": payload.get("context_refresh", {}),
        "mode": payload.get("mode"),
        "window": payload.get("window"),
        "queries": query_rows,
        "sources": source_rows,
        "totals": {
            "raw_records": int(payload.get("counts", {}).get("searched", 0)),
            "deduplicated": int(payload.get("counts", {}).get("deduplicated", len(records))),
            "relevant": relevant_total,
            "selected": len(selected_keys),
            "planned_queries": len(plan.get("queries", [])),
            "attempted_queries": sum(row["attempted"] for row in query_rows),
            "successful_queries": sum(row["successful"] for row in query_rows),
            "active_sources": active_sources,
            "attempted_sources": available_sources,
            "overlap_records": sum(len(record.get("sources", [])) > 1 for record in records),
        },
        "operational_search_saturation": {
            "level": level,
            "reason": reason,
            "limited_sources": incomplete,
            "unattempted_core_queries": sorted(unattempted_core),
            "low_marginal_gain_streak": low_streak,
            "marginal_gain_threshold": threshold,
            "claim_boundary": "Operational indicator only; it does not prove that all relevant literature was found.",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate operational ecology search coverage")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=SKILL_DIR / "config" / "interests.yaml")
    parser.add_argument("--selected", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = load_config(args.input)
    config = load_config(args.config)
    selected = load_config(args.selected) if args.selected else None
    report = build_coverage_report(payload, config, selected)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"wrote={args.output} saturation={report['operational_search_saturation']['level']} "
        f"relevant={report['totals']['relevant']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
