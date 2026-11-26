#!/usr/bin/env python3
"""Merge a run-scoped project/chat research profile into the stable base config."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any


LIST_FIELDS = (
    "secondary_topics",
    "domain_keywords",
    "negative_keywords",
    "target_journals",
)


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def unique_strings(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def quote_europepmc_term(value: str) -> str:
    text = " ".join(value.strip().split()).replace('"', "")
    if not text:
        return ""
    return f'"{text}"' if " " in text else text


def merge_topic(base_topic: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    topic = copy.deepcopy(base_topic)
    if "label" in overlay and str(overlay["label"]).strip():
        topic["label"] = str(overlay["label"]).strip()
    if "weight" in overlay:
        weight = float(overlay["weight"])
        if not 0.1 <= weight <= 3.0:
            raise ValueError("Topic weight must be between 0.1 and 3.0")
        topic["weight"] = weight
    if "category" in overlay and str(overlay["category"]).strip():
        topic["category"] = str(overlay["category"]).strip()
    topic["keywords"] = unique_strings(
        list(topic.get("keywords", [])) + list(overlay.get("keywords", []))
    )
    if not topic["keywords"]:
        raise ValueError("Every context topic needs at least one keyword")
    topic.setdefault("label", "Project context")
    topic.setdefault("weight", 1.0)
    return topic


def build_effective_config(
    base: dict[str, Any], profile: dict[str, Any]
) -> dict[str, Any]:
    config = copy.deepcopy(base)
    if int(profile.get("schema_version", 1)) != 1:
        raise ValueError("Unsupported context profile schema_version")

    context_applied = bool(profile.get("context_applied", True))
    mode = str(profile.get("retrieval_mode", "extend")).strip().casefold()
    if mode not in {"extend", "focus"}:
        raise ValueError("retrieval_mode must be 'extend' or 'focus'")

    context_queries = unique_strings(list(profile.get("retrieval_queries", [])))
    if context_applied and mode == "focus" and not context_queries:
        raise ValueError("focus mode requires at least one retrieval query")
    if context_applied:
        base_queries = [] if mode == "focus" else list(config.get("retrieval_queries", []))
        # Put contextual queries first so broad defaults cannot crowd them out.
        config["retrieval_queries"] = unique_strings(context_queries + base_queries)[:20]

        base_scholar = [] if mode == "focus" else list(
            config.get("google_scholar_queries", [])
        )
        config["google_scholar_queries"] = unique_strings(
            context_queries + base_scholar
        )[:5]

        for query_field in ("openalex_queries", "semantic_scholar_queries"):
            base_source_queries = (
                [] if mode == "focus" else list(config.get(query_field, []))
            )
            config[query_field] = unique_strings(
                context_queries + base_source_queries
            )[:5]

        contextual_wos = [f"TS=({query})" for query in context_queries]
        base_wos = [] if mode == "focus" else list(config.get("wos_queries", []))
        config["wos_queries"] = unique_strings(contextual_wos + base_wos)[:5]

        base_europepmc = str(config.get("europepmc_query", "")).strip()
        extra_terms = [
            quote_europepmc_term(str(value))
            for value in list(profile.get("europepmc_terms", []))
        ]
        extra_terms = [value for value in extra_terms if value]
        if mode == "focus" and extra_terms:
            config["europepmc_query"] = "(" + " OR ".join(extra_terms) + ")"
        elif extra_terms:
            config["europepmc_query"] = (
                f"({base_europepmc} OR " + " OR ".join(extra_terms) + ")"
            )

        topics = config.setdefault("primary_topics", {})
        for key, value in dict(profile.get("primary_topics", {})).items():
            topic_key = str(key).strip()
            if not topic_key or not isinstance(value, dict):
                raise ValueError("primary_topics must map non-empty keys to objects")
            topics[topic_key] = merge_topic(dict(topics.get(topic_key, {})), value)

        for field in LIST_FIELDS:
            config[field] = unique_strings(
                list(config.get(field, [])) + list(profile.get(field, []))
            )

    evidence = profile.get("evidence", {})
    if not isinstance(evidence, dict):
        raise ValueError("evidence must be an object")
    config["_context_modules"] = copy.deepcopy(profile.get("context_modules", {})) if context_applied and mode == "extend" else {}
    config["_context"] = {
        "applied": context_applied,
        "retrieval_mode": mode,
        "focus_summary": str(profile.get("focus_summary", "")).strip(),
        "chat_basis": unique_strings(list(evidence.get("chat_basis", []))),
        "project_files": unique_strings(list(evidence.get("project_files", []))),
        "source_ids": unique_strings([str(item.get("source_id", "")) for item in evidence.get("source_records", [])]),
        "refresh": copy.deepcopy(profile.get("_context_refresh", {})),
    }
    return config


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Merge a project/chat ecology profile into the base interests config"
    )
    value.add_argument("--base", type=Path, required=True)
    value.add_argument("--profile", type=Path, required=True)
    value.add_argument("--output", type=Path, required=True)
    return value


def main() -> int:
    args = parser().parse_args()
    effective = build_effective_config(load_object(args.base), load_object(args.profile))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(effective, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    context = effective["_context"]
    print(
        f"wrote={args.output} context_applied={str(context['applied']).lower()} "
        f"retrieval_mode={context['retrieval_mode']} "
        f"queries={len(effective.get('retrieval_queries', []))}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
