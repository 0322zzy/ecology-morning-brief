"""Check rendered paper entries against the evidence actually available."""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlsplit

from brief_engine import clean_markup, normalize_doi


LABELS = {
    "作者摘要": "abstract",
    "标题+元数据": "metadata",
    "开放全文": "full_text",
}
NUMBERS = re.compile(r"(?<![\w./-])\d+(?:,\d{3})*(?:\.\d+)?%?|(?<=[\u3400-\u9fff])\d+(?:,\d{3})*(?:\.\d+)?%?")
FIGURES = re.compile(r"(?:图(?:表)?\s*[SＳ]?\d+[A-Za-z]?|表\s*[SＳ]?\d+[A-Za-z]?|\b(?:fig(?:ure)?\.?|table)\s*[Ss]?\d+[A-Za-z]?)", re.IGNORECASE)
MEASURED = re.compile(r"\d(?:[\d,.]*\d)?(?:\s*(?:%|°\s*[CF]|℃|倍|[篇种只个名条岁毫米厘米千米]))|(?:\bn\s*=|样本量[为：:]?\s*|效应值[为：:]?\s*)\d+(?:\.\d+)?")
HEADING = re.compile(r"^#{3,6}\s+[^\n]*\[([^\n]+?)\]\((https?://[^\s)]+)\)[^\n]*\n", re.M)
LABEL = re.compile(r"\[证据\s*[:：]\s*([^\]\n]+)\]")


def canonical_key(url):
    if urlsplit(url).hostname in {"doi.org", "dx.doi.org"}:
        return normalize_doi(url)
    return url.rstrip("/")


def _norm(text):
    return unicodedata.normalize("NFKC", clean_markup(text)).casefold()


def _numbers(text):
    return {m.group().replace(",", "").rstrip("%") for m in NUMBERS.finditer(text)}


def _full_text(row):
    value = row.get("full_text_evidence") or {}
    return value if all(str(value.get(k) or "").strip() for k in ("url", "location", "excerpt")) else {}


def check_report(records, report):
    errors = []
    findings = []
    expected = {canonical_key(r.get("url") or ""): r for r in records if r.get("url")}
    expected.update({normalize_doi(r["doi"]): r for r in records if r.get("doi")})
    matches = list(HEADING.finditer(report))
    seen = set()
    for idx, match in enumerate(matches):
        row = expected.get(canonical_key(match[2]))
        if row is None:
            errors.append(f"Paper heading not found in selection: {match[1]}")
            continue
        identity = row.get("doi") or row["url"]
        if identity in seen:
            errors.append(f"Duplicate paper heading: {identity}")
            continue
        seen.add(identity)
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(report)
        body = re.split(r"^#{1,2}\s", report[match.end():end], maxsplit=1, flags=re.M)[0].strip()
        blocks = re.split(r"\n\s*\n", body, maxsplit=1)
        labels = LABEL.findall(blocks[0])
        kind = LABELS.get(labels[0]) if len(labels) == 1 else None
        if kind is None:
            errors.append(f"{identity}: expected one evidence label in the metadata line")
            continue
        if kind == "abstract" and not str(row.get("abstract") or "").strip():
            errors.append(f"{identity}: author's abstract is missing")
            continue
        fulltext = _full_text(row) if kind == "full_text" else {}
        if kind == "full_text" and not fulltext:
            errors.append(f"{identity}: full-text label needs a read excerpt, source URL and location")
            continue
        support = _norm(" ".join([
            str(row.get("title") or ""), str(row.get("published_date") or ""),
            str(row.get("online_date") or ""), str(row.get("abstract") or "") if kind != "metadata" else "",
            str(fulltext.get("excerpt") or ""),
        ]))
        if len(blocks) != 2 or not blocks[1].strip():
            errors.append(f"{identity}: no interpretation paragraph after metadata")
            continue
        interpretation = _norm(blocks[1])
        supported_numbers = _numbers(support)
        figures = {x.group().replace(" ", "") for x in FIGURES.finditer(support)}
        suspect_figures = sorted({x.group() for x in FIGURES.finditer(interpretation) if x.group().replace(" ", "") not in figures})
        suspect_numbers = sorted({num for m in MEASURED.finditer(interpretation) for num in _numbers(m.group()) if num not in supported_numbers})
        if suspect_figures or suspect_numbers:
            findings.append({"paper": str(identity), "review": "需复核", "figure_refs": suspect_figures,
                             "numbers": suspect_numbers, "evidence": labels[0]})
    missing = sorted({r.get("doi") or r.get("url") for r in records} - seen, key=str)
    errors.extend(f"Selected paper missing from report: {key}" for key in missing)
    return {"ok": not errors, "checked": len(seen), "errors": errors, "needs_review": findings}
