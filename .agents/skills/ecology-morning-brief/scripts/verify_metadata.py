#!/usr/bin/env python3
"""Verify finalist metadata against Crossref without treating absence as exclusion."""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.parse
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from brief_engine import (APIClient, RequestFailure, _openalex_abstract, clean_markup,
                          load_config, normalize_doi, normalize_title, read_page)


SKILL_DIR = Path(__file__).resolve().parents[1]


def _crossref_year(item: dict[str, Any]) -> int | None:
    for field in ("published-online", "published-print", "published", "issued", "created"):
        parts = (item.get(field) or {}).get("date-parts", [])
        if parts and parts[0]:
            try:
                return int(parts[0][0])
            except (TypeError, ValueError, IndexError):
                continue
    return None


def _record_year(record: dict[str, Any]) -> int | None:
    for field in ("online_date", "published_date", "indexed_date"):
        match = re.search(r"(?:19|20)\d{2}", str(record.get(field, "")))
        if match:
            return int(match.group(0))
    return None


def _title(item: dict[str, Any]) -> str:
    values = item.get("title") or []
    return str(values[0]) if values else ""


def _journal(item: dict[str, Any]) -> str:
    values = item.get("container-title") or []
    return str(values[0]) if values else ""


def _authors(item: dict[str, Any]) -> list[str]:
    return [
        " ".join(value for value in (str(author.get("given", "")), str(author.get("family", ""))) if value).strip()
        for author in item.get("author", []) or []
    ]


def evaluate_match(record: dict[str, Any], item: dict[str, Any], *, exact_doi_lookup: bool) -> dict[str, Any]:
    requested_doi = normalize_doi(str(record.get("doi", "")))
    canonical_doi = normalize_doi(str(item.get("DOI", "")))
    requested_title = normalize_title(str(record.get("title", "")))
    canonical_title = normalize_title(_title(item))
    similarity = SequenceMatcher(None, requested_title, canonical_title).ratio() if requested_title and canonical_title else 0.0
    record_year, canonical_year = _record_year(record), _crossref_year(item)
    flags: list[str] = []
    status = "probable_match"
    if exact_doi_lookup and requested_doi and canonical_doi == requested_doi and similarity >= 0.9:
        status = "exact_match"
    elif similarity < 0.72:
        status = "title_mismatch"
        flags.append("title_mismatch")
    if record_year and canonical_year and abs(record_year - canonical_year) > 1:
        flags.append("year_mismatch")
    if not requested_doi:
        flags.append("doi_missing")
    if record.get("version_status") == "published_with_preprint":
        flags.extend(["preprint_published_pair", "duplicate_publication"])
    alerts = []
    if status == "exact_match":
        for field in ("updated-by", "update-to"):
            for update in item.get(field, []) or []:
                kind = str(update.get("type") or update.get("label") or "").casefold()
                if any(term in kind for term in ("retract", "concern", "withdrawal")):
                    alerts.append(f"{kind} (Crossref)")
    return {
        "status": status,
        "publication_alerts": list(dict.fromkeys(alerts)),
        "author_abstract": clean_markup(str(item.get("abstract") or "")) if status == "exact_match" else "",
        "flags": list(dict.fromkeys(flags)),
        "title_similarity": round(similarity, 3),
        "canonical": {
            "doi": canonical_doi,
            "title": _title(item),
            "year": canonical_year,
            "journal": _journal(item),
            "authors": _authors(item),
            "record_type": str(item.get("type", "")),
        },
    }


def verify_record(client: APIClient, record: dict[str, Any]) -> dict[str, Any]:
    doi = normalize_doi(str(record.get("doi", "")))
    try:
        if doi:
            encoded = urllib.parse.quote(doi, safe="")
            payload = client.get_json("crossref_verification", f"https://api.crossref.org/works/{encoded}")
            item = payload.get("message") or {}
            if isinstance(item, dict) and item:
                return evaluate_match(record, item, exact_doi_lookup=True)
        payload = client.get_json(
            "crossref_verification",
            "https://api.crossref.org/works",
            {"query.bibliographic": str(record.get("title", "")), "rows": 3},
        )
        items = read_page(client, payload, ("message", "items"))
        if not items:
            return {
                "status": "not_found",
                "flags": (["doi_missing"] if not doi else []),
                "canonical": {},
                "note": "Crossref has no usable record; the paper was retained.",
            }
        best = max(
            items,
            key=lambda item: SequenceMatcher(
                None,
                normalize_title(str(record.get("title", ""))),
                normalize_title(_title(item)),
            ).ratio(),
        )
        return evaluate_match(record, best, exact_doi_lookup=False)
    except Exception as exc:
        return {
            "status": "unavailable",
            "flags": (["doi_missing"] if not doi else []),
            "canonical": {},
            "note": f"Crossref verification unavailable; the paper was retained: {exc}",
        }


def recover_author_abstract(client, record, excluded):
    """Fetch author abstracts by exact DOI, not snippets, generated text or another version."""
    doi = normalize_doi(str(record.get("doi") or ""))
    if not doi:
        return "", "", []
    target = urllib.parse.quote(doi, safe="/")
    key = os.getenv("OPENALEX_API_KEY", "").strip()
    headers = {"Authorization": f"Bearer {key}"} if key else None
    alerts = []
    if "openalex" not in excluded:
        try:
            work = client.get_json("openalex_abstract", f"https://api.openalex.org/works/doi:{target}",
                                   {"select": "doi,title,is_retracted,abstract_inverted_index"}, headers)
            if normalize_doi(str(work.get("doi") or "")) == doi:
                if work.get("is_retracted") is True:
                    alerts.append("retracted (OpenAlex)")
                abstract = _openalex_abstract(work.get("abstract_inverted_index"))
                if abstract:
                    return abstract, "OpenAlex author abstract", alerts
        except RuntimeError as exc:
            if isinstance(exc, RequestFailure) and exc.reason in {"authentication_or_access", "rate_limited", "rate_limit_cooldown"}:
                excluded.add("openalex")
    if "europepmc" not in excluded:
        try:
            payload = client.get_json("europepmc_abstract", "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                                      {"query": f'DOI:"{doi}"', "format": "json", "resultType": "core", "pageSize": 5})
            matches = read_page(client, payload, ("resultList", "result"), total=payload.get("hitCount"),
                                empty_without_items=True)
            for item in matches:
                if normalize_doi(str(item.get("doi") or "")) == doi and item.get("abstractText"):
                    return clean_markup(item["abstractText"]), "Europe PMC author abstract", alerts
        except RuntimeError as exc:
            if isinstance(exc, RequestFailure) and exc.reason in {"authentication_or_access", "rate_limited", "rate_limit_cooldown"}:
                excluded.add("europepmc")
    return "", "", alerts


def verify_records(client: APIClient, records: list[dict[str, Any]], max_abstract_lookups: int = 0) -> list[dict[str, Any]]:
    verified: list[dict[str, Any]] = []
    remaining = max_abstract_lookups
    excluded = set()
    for raw in records:
        record = dict(raw)
        verification = verify_record(client, record)
        author_abstract = verification.pop("author_abstract", "")
        record["metadata_verification"] = verification
        if not record.get("abstract") and author_abstract:
            record.update(abstract=author_abstract, evidence_level="abstract-supported",
                          abstract_source="Crossref author abstract")
        elif not record.get("abstract") and record.get("doi") and remaining > 0 and verification["status"] != "title_mismatch":
            remaining -= 1
            abstract, source, alerts = recover_author_abstract(client, record, excluded)
            verification.setdefault("publication_alerts", []).extend(alerts)
            if abstract:
                record.update(abstract=abstract, abstract_source=source, evidence_level="abstract-supported")
        verified.append(record)
    return verified


def main() -> int:
    parser = argparse.ArgumentParser(description="Crossref metadata verification for selected ecology papers")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=SKILL_DIR / "config" / "interests.yaml")
    parser.add_argument("--max-abstract-lookups", type=int, default=8, help="Maximum missing-abstract records looked up by DOI")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = load_config(args.input)
    config = load_config(args.config)
    key = "articles" if isinstance(payload.get("articles"), list) else "candidates"
    if not isinstance(payload.get(key), list):
        raise ValueError("Input must contain an articles or candidates list")
    client = APIClient(config)
    payload[key] = verify_records(client, payload[key], max_abstract_lookups=args.max_abstract_lookups)
    payload["metadata_requests"] = client.query_attempts
    summary: dict[str, int] = {}
    for record in payload[key]:
        status = str(record.get("metadata_verification", {}).get("status", "unknown"))
        summary[status] = summary.get(status, 0) + 1
    payload["metadata_verification_summary"] = summary
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote={args.output} verified={len(payload[key])} statuses={summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
