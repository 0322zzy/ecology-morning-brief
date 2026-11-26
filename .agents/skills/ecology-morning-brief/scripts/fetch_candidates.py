#!/usr/bin/env python3
"""Run the v2 multi-strategy retrieval pipeline and emit an auditable shortlist."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from brief_engine import (
    APIClient,
    Article,
    HistoryDB,
    SourceUnavailable,
    article_in_window,
    classify_relevance,
    collect_arxiv,
    collect_biorxiv,
    collect_crossref,
    collect_europepmc,
    collect_google_scholar,
    collect_openalex,
    collect_openalex_targets,
    collect_semantic_scholar,
    collect_webofscience,
    configure_logging,
    deduplicate,
    default_state_dir,
    load_config,
    normalize_title,
    journal_group,
    is_notice,
    score_article,
    safe_parameters,
)
from journal_watch import collect_journal_watch
from search_planner import VALID_MODES, build_search_plan
from source_router import apply_route_to_config, route_search_plan
from supplemental_sources import collect_pubmed, recovery_targets


SKILL_DIR = Path(__file__).resolve().parents[1]


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Fetch and shortlist ecology papers for Codex")
    value.add_argument("--date", help="Report date YYYY-MM-DD")
    value.add_argument("--lookback", type=int, help="Lookback hours")
    value.add_argument("--mode", choices=VALID_MODES)
    value.add_argument("--max-candidates", type=int, help="Candidate output limit")
    value.add_argument("--config", type=Path, default=SKILL_DIR / "config" / "interests.yaml")
    value.add_argument("--profile", type=Path, help="Run-scoped profile used when auto-building a plan")
    value.add_argument("--plan", type=Path, help="Pre-built ecology-search-plan.json")
    value.add_argument("--state-dir", type=Path, default=default_state_dir())
    value.add_argument("--output", type=Path, help="JSON output file; defaults to stdout")
    value.add_argument("--audit-output", type=Path, help="Audit JSON (default: alongside --output)")
    value.add_argument("--coverage-output", type=Path, help="Write ecology-coverage-report.json")
    value.add_argument("--skip-context-refresh-check", action="store_true", help="Isolated provider validation only; do not use for a scheduled daily brief")
    value.add_argument("--enable-boosters", action="store_true", help="Force eligible boosters")
    value.add_argument(
        "--source",
        action="append",
        choices=[
            "crossref", "biorxiv", "europepmc", "openalex",
            "semantic_scholar", "arxiv", "webofscience", "google_scholar", "journal_watch",
            "pubmed_supplement",
        ],
        help="Limit to one or more sources (repeatable)",
    )
    return value


COLLECTORS = {
    "journal_watch": collect_journal_watch,
    "crossref": collect_crossref,
    "biorxiv": collect_biorxiv,
    "europepmc": collect_europepmc,
    "openalex": collect_openalex,
    "semantic_scholar": collect_semantic_scholar,
    "arxiv": collect_arxiv,
    "webofscience": collect_webofscience,
    "google_scholar": collect_google_scholar,
}


def _run_sources(
    names: list[str],
    client: APIClient,
    config: dict[str, Any],
    start: datetime,
    end: datetime,
    statuses: dict[str, dict[str, object]],
) -> list[Article]:
    collected: list[Article] = []
    for name in names:
        collector = COLLECTORS[name]
        source_config = config.get("sources", {}).get(name, {})
        old_channel = getattr(client, "channel", None)
        if old_channel is not None:
            client.channel = name
        try:
            articles, partial, error = collector(client, config, start, end)
            collected.extend(articles)
            statuses[name] = {
                "status": "partial" if partial else "ok",
                "count": len(articles),
                "class": source_config.get("class", "core"),
            }
            if error:
                statuses[name]["error"] = error
        except SourceUnavailable as exc:
            statuses[name] = {
                "status": "unavailable", "count": 0, "error": str(exc),
                "class": source_config.get("class", "booster"),
            }
        except Exception as exc:
            statuses[name] = {
                "status": "failed", "count": 0, "error": str(exc),
                "class": source_config.get("class", "core"),
            }
        finally:
            if old_channel is not None:
                client.channel = old_channel
        provider = str(source_config.get("provider", "")).strip()
        if provider:
            statuses[name]["provider"] = provider
    return collected


def _rank(articles: list[Article], config: dict[str, Any]) -> list[Article]:
    return sorted(
        (classify_relevance(score_article(article, config), config) for article in articles),
        key=lambda article: (
            article.relevance_class == "direct_hit",
            article.relevance_class == "adjacent_topic",
            article.rule_score,
            bool(article.abstract),
        ),
        reverse=True,
    )


def _shortlist(
    ranked: list[Article], config: dict[str, Any], candidate_limit: int, threshold: float
) -> list[Article]:
    unseen = [article for article in ranked if not article.previously_reported and not is_notice(article)]
    # Preserve distinct discovery channels even when a paper has weak topic-keyword overlap.
    pools = {"cns": [], "journal": [], "method": [], "direct": [], "wildcard": [], "preprint": []}
    for article in unseen:
        if article.is_preprint:
            if article.rule_score >= threshold:
                pools["preprint"].append(article)
        elif "journal_watch" in article.discovery_methods:
            pools["cns" if journal_group(article.journal, config) == "cns" else "journal"].append(article)
        elif article.relevance_class == "method_transfer" and article.rule_score >= threshold * 0.5:
            pools["method"].append(article)
        elif article.relevance_class == "serendipity" and article.serendipity_bridges and article.rule_score >= threshold * 0.5:
            pools["wildcard"].append(article)
        elif article.rule_score >= threshold:
            pools["direct"].append(article)
    # Interleave channels so journal/method discovery survives the final pool limit.
    result: list[Article] = []
    fraction = min(0.4, max(0.0, float(config.get("search", {}).get("serendipity_fraction", 0.15))))
    wildcard_limit = round(candidate_limit * fraction)
    wildcard_count = 0
    # Round-robin within journal groups too, preserving lower-volume journals.
    for lane in ("cns", "journal"):
        by_journal: dict[str, list[Article]] = {}
        for article in pools[lane]:
            by_journal.setdefault(article.journal, []).append(article)
        pools[lane] = []
        while by_journal:
            for journal in list(by_journal):
                pools[lane].append(by_journal[journal].pop(0))
                if not by_journal[journal]:
                    del by_journal[journal]
    order = ("cns", "direct", "method", "journal", "cns", "wildcard")
    while any(pools[key] for key in order) and len(result) < candidate_limit:
        added = False
        for key in order:
            if key == "wildcard" and wildcard_count >= wildcard_limit:
                pools[key] = []
            if pools[key] and len(result) < candidate_limit:
                result.append(pools[key].pop(0))
                wildcard_count += key == "wildcard"
                added = True
        if not added:
            break
    # Preprint discovery gets a separate small candidate allowance; delivery caps are checked later.
    allowance = int(config.get("delivery", {}).get("max_preprints", 3)) * 3
    return (result + pools["preprint"][:allowance])[:candidate_limit]


def _audit_records(unique: list[Article], threshold: float) -> list[dict[str, Any]]:
    return [
        {
            "key": article.doi or article.normalized_title,
            "doi": article.doi,
            "title": article.title,
            "sources": article.sources,
            "query_ids": article.query_ids,
            "rule_score": article.rule_score,
            "relevance_class": article.relevance_class,
            "relevant": article.rule_score >= threshold or article.relevance_class in {
                "direct_hit", "method_transfer", "serendipity"
            },
            "candidate": not article.previously_reported,
        }
        for article in unique
    ]


def validate_context_refresh(config: dict[str, Any], profile: dict[str, Any] | None, now: datetime | None = None, *, report_date: str | None = None) -> dict[str, Any]:
    from refresh_context import instant
    policy = config.get("context_refresh", {})
    refresh = (profile or {}).get("_context_refresh", config.get("_context", {}).get("refresh", {}))
    if not policy.get("enabled"):
        return refresh
    if not refresh.get("checked_at"):
        raise ValueError("Daily context refresh audit missing; run the skill's chat/project refresh before retrieval")
    if report_date and refresh.get("report_date") != report_date:
        raise ValueError("This daily report needs a context refresh audit for its own report date")
    current = now or datetime.now(timezone.utc)
    age_hours = (current - instant(refresh["checked_at"])).total_seconds()/3600
    if age_hours < -1 or age_hours > float(policy.get("max_age_hours", 36)):
        raise ValueError("Context refresh audit is stale or future-dated; refresh sources before daily retrieval")
    if refresh.get("status") not in {"updated", "no_change", "degraded"}:
        raise ValueError("Context refresh has no auditable result")
    return refresh


def _supplement(client, name, collect, statuses):
    previous_channel = client.channel
    client.channel = name
    try:
        articles, partial, error = collect()
        status = {"status": "partial" if partial else "ok", "count": len(articles)}
        if error:
            status["error"] = error
        statuses[name] = status
        return articles
    except RuntimeError as exc:
        statuses[name] = {"status": "failed", "count": 0, "error": str(exc)}
        return []
    finally:
        client.channel = previous_channel


def _source_counts(attempts, stages):
    counts = {}
    for attempt in attempts:
        source = attempt["source"]
        counts.setdefault(source, Counter()).update(
            request_records=1, http_attempts=len(attempt.get("attempts", [])),
            successful_pages=int(attempt["status"] == "ok"),
            failed_or_skipped=int(attempt["status"] != "ok"),
            api_returned_records=attempt.get("returned_records") or 0,
            pages_with_record_count=int(attempt.get("returned_records") is not None),
        )
    for stage, records in stages.items():
        for record in records:
            for source in record.sources:
                counts.setdefault(source, Counter())[stage] += 1
    return {source: dict(values) for source, values in sorted(counts.items())}


def _snapshot(config, plan, path, profile_path):
    def scrub(value):
        if isinstance(value, dict):
            return {k: scrub(v) for k, v in safe_parameters(value).items()}
        if isinstance(value, list):
            return [scrub(v) for v in value]
        return value

    scripts = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((SKILL_DIR / "scripts").glob("*.py"))
        if not p.name.startswith(("test_", "self_test"))
    }
    effective = scrub(config)
    snapshot = {"inputs": {
        "supplied_config_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "bundled_config_sha256": hashlib.sha256((SKILL_DIR / "config" / "interests.yaml").read_bytes()).hexdigest(),
        "profile_sha256": hashlib.sha256(profile_path.read_bytes()).hexdigest() if profile_path else None,
    }, "effective_config": effective, "plan": scrub(plan), "script_sha256": scripts}
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    snapshot["snapshot_sha256"] = hashlib.sha256(encoded).hexdigest()
    return snapshot


def main() -> int:
    args = parser().parse_args()
    started = time.monotonic()
    base_config = load_config(args.config)
    profile = load_config(args.profile) if args.profile else None
    plan = (
        load_config(args.plan)
        if args.plan
        else build_search_plan(
            base_config, profile, mode=args.mode, report_date=args.date, lookback=args.lookback
        )
    )
    route = route_search_plan(plan, base_config)
    config = apply_route_to_config(base_config, plan, route)
    configure_logging(args.state_dir)
    start = datetime.fromisoformat(str(plan["window"]["start"]))
    end = datetime.fromisoformat(str(plan["window"]["end"]))
    report_day = str(plan["report_date"])
    mode = str(plan.get("mode", "daily"))
    context_refresh = (profile or {}).get("_context_refresh", config.get("_context", {}).get("refresh", {}))
    if mode == "daily" and not args.skip_context_refresh_check:
        context_refresh = validate_context_refresh(config, profile, report_date=report_day)
    selected_sources = set(args.source or [*COLLECTORS, "pubmed_supplement"])

    statuses: dict[str, dict[str, object]] = {}
    core_sources: list[str] = []
    booster_sources: list[str] = []
    for name in COLLECTORS:
        details = route.get("sources", {}).get(name, {})
        settings = base_config.get("sources", {}).get(name, {})
        if name not in selected_sources or not settings.get("enabled", False):
            statuses[name] = {
                "status": "disabled", "count": 0, "class": settings.get("class", "core")
            }
        elif details.get("active_core"):
            core_sources.append(name)
        elif details.get("active_booster"):
            booster_sources.append(name)
            statuses[name] = {"status": "deferred", "count": 0, "class": "booster"}
        else:
            statuses[name] = {
                "status": "not_routed", "count": 0, "class": settings.get("class", "core")
            }

    client = APIClient(config)
    collected = _run_sources(core_sources, client, config, start, end, statuses)
    window_verified = {"webofscience", "google_scholar"}
    core_window = [
        article for article in collected
        if window_verified.intersection(article.sources) or article_in_window(article, start, end)
    ]
    core_unique = deduplicate(
        core_window, float(config.get("title_similarity_threshold", 0.94))
    )
    preliminary = _rank(core_unique, config)
    threshold = float(config.get("candidate_score_threshold", 20))
    prelim_relevant = sum(article.rule_score >= threshold for article in preliminary)
    booster_config = config.get("search", {}).get("boosters", {})
    booster_allowed = args.enable_boosters or bool(booster_config.get(mode, False))
    booster_trigger = int(booster_config.get("trigger_min_candidates", 10))
    recovery = config.get("search", {}).get("daily_recovery", {})
    focused = (plan.get("context") or {}).get("retrieval_mode") == "focus"
    adaptive = mode == "daily" and not focused and recovery.get("enabled", True)
    attempts = list(client.query_attempts)
    targets = (recovery_targets(config, plan, attempts, preliminary)
               if adaptive and "openalex" in booster_sources and not args.enable_boosters else [])
    normal_boosters = [source for source in booster_sources if not (targets and source == "openalex")]
    booster_reason = "not enabled for mode"
    if booster_allowed and normal_boosters and (
        args.enable_boosters or mode != "daily" or prelim_relevant < booster_trigger
    ):
        booster_reason = (
            "deep mode" if mode != "daily"
            else f"core relevant candidates below {booster_trigger}"
        )
        collected.extend(_run_sources(normal_boosters, client, config, start, end, statuses))
    elif normal_boosters:
        booster_reason = "core coverage sufficient" if booster_allowed else "not enabled for mode"
    if targets:
        collected.extend(_supplement(client, "openalex", lambda: collect_openalex_targets(
            client, start, end, targets, page_size=min(50, max(1, int(recovery.get("page_size", 25)))),
        ), statuses))
        booster_reason = "bounded OpenAlex check for journal or subject coverage gaps"
    pubmed_requested = args.source and "pubmed_supplement" in args.source
    epmc_failed = any(attempt["source"] == "europepmc" and attempt["status"] != "ok" for attempt in attempts)
    if "pubmed_supplement" in selected_sources and (
        pubmed_requested or (adaptive and recovery.get("pubmed_on_europepmc_failure", True) and epmc_failed)
    ):
        collected.extend(_supplement(client, "pubmed_supplement", lambda: collect_pubmed(client, start, end), statuses))

    in_window = [
        article for article in collected
        if window_verified.intersection(article.sources) or article_in_window(article, start, end)
    ]
    unique = deduplicate(
        in_window, float(config.get("title_similarity_threshold", 0.94))
    )
    ranked = _rank(unique, config)
    database = HistoryDB(args.state_dir)
    try:
        for article in ranked:
            database.upsert_seen(article)
    finally:
        database.close()

    candidate_limit = args.max_candidates or int(config.get("candidate_limit", 50))
    candidates = _shortlist(ranked, config, candidate_limit, threshold)
    raw_query_counts = Counter(
        query_id for article in collected for query_id in article.query_ids
    )
    payload: dict[str, Any] = {
        "schema_version": 2,
        "report_date": report_day,
        "mode": mode,
        "window": plan["window"],
        "config": {
            "delivery": config.get("delivery", {}),
            "top_articles": int(config.get("top_articles", 12)),
            "top_priority": int(config.get("top_priority", 3)),
            "candidate_score_threshold": threshold,
            "serendipity_fraction": float(
                config.get("search", {}).get("serendipity_fraction", 0.15)
            ),
        },
        "context_refresh": context_refresh,
        "search_plan": plan,
        "source_route": route,
        "sources": statuses,
        "booster_decision": {
            "invoked": any(
                statuses.get(name, {}).get("status")
                in {"ok", "partial", "failed", "unavailable"}
                for name in booster_sources
                if name in statuses
            ),
            "reason": booster_reason,
        },
        "counts": {
            "searched": len(collected),
            "date_filtered": len(in_window),
            "deduplicated": len(unique),
            "scientifically_prescreened": len(ranked),
            "candidate": len(candidates),
            "published_candidates": sum(article.publication_status == "published" for article in candidates),
            "preprint_candidates": sum(article.is_preprint for article in candidates),
            "journal_watch_candidates": sum("journal_watch" in article.discovery_methods for article in candidates),
            "serendipity_candidates": sum(
                article.relevance_class == "serendipity" for article in candidates
            ),
        },
        "retrieval_audit": {
            "query_attempts": client.query_attempts,
            "raw_query_counts": dict(raw_query_counts),
            "raw_source_counts": {
                name: int(value.get("count", 0)) for name, value in statuses.items()
            },
            "records": _audit_records(ranked, threshold),
        },
        "state_dir": str(args.state_dir.resolve()),
        "duration_seconds": round(time.monotonic() - started, 2),
        "candidates": [article.serializable() for article in candidates],
    }
    audit_path = args.audit_output or (args.output.with_suffix(".audit.json") if args.output else None)
    if audit_path:
        if audit_path.resolve() in {path.resolve() for path in (args.output, args.coverage_output) if path}:
            raise ValueError("Audit and candidate/coverage output paths must be distinct")
        snapshot = _snapshot(config, plan, args.config, args.profile)
        payload["run_snapshot"] = {"sha256": snapshot["snapshot_sha256"], "audit_file": str(audit_path.resolve())}
        audit = {
            "report_date": report_day, "window": plan["window"], "sources": statuses,
            "source_counts": _source_counts(client.query_attempts, {
                "normalized": collected, "inside_window": in_window, "merged": unique,
                "unseen": [a for a in ranked if not a.previously_reported], "shortlisted": candidates,
            }),
            "counts": payload["counts"], "requests": client.query_attempts,
            "snapshot": snapshot,
            "candidate_file": str(args.output.resolve()) if args.output else None,
            "counting_note": "Provider records can overlap. Some providers lack a raw page count. A successful empty page differs from failed or skipped requests.",
        }
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.coverage_output:
        from evaluate_coverage import build_coverage_report

        coverage = build_coverage_report(payload, config)
        args.coverage_output.parent.mkdir(parents=True, exist_ok=True)
        args.coverage_output.write_text(
            json.dumps(coverage, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        payload["coverage_summary"] = coverage["operational_search_saturation"]

    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
        print(
            f"wrote={args.output} mode={mode} searched={len(collected)} "
            f"deduplicated={len(unique)} candidate={len(candidates)}",
            file=sys.stderr,
        )
    else:
        print(encoded)
    attempted = [
        status for status in statuses.values()
        if status.get("status") not in {"disabled", "not_routed", "deferred"}
    ]
    all_failed = bool(attempted) and all(
        status.get("status") in {"failed", "unavailable"} for status in attempted
    )
    return 2 if all_failed or not attempted else 0


if __name__ == "__main__":
    raise SystemExit(main())
