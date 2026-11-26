#!/usr/bin/env python3
"""Check publication mix and delivery size after scientific editorial review."""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
from brief_engine import Article, load_config, normalize_article, journal_group, is_notice
from report_evidence import check_report


def check_selection(records: list[dict], config: dict) -> dict:
    policy = config.get('delivery', {})
    fields = Article.__dataclass_fields__
    articles = [normalize_article(Article(**{k:v for k,v in row.items() if k in fields})) for row in records]
    n = len(articles)
    preprints = sum(a.is_preprint for a in articles)
    published = sum(a.publication_status == 'published' for a in articles)
    title_only = sum(not a.abstract for a in articles)
    cns = sum(journal_group(a.journal, config) == "cns" for a in articles)
    errors, warnings = [], []
    keys = [a.doi or a.normalized_title for a in articles]
    titles = [a.normalized_title for a in articles]
    if len(set(keys)) != n or len(set(titles)) != n:
        errors.append('Duplicate DOI/title identities in selected records')
    if any(a.metadata_verification.get('status') == 'title_mismatch' for a in articles):
        errors.append('Unresolved DOI/title verification mismatch')
    if any(a.previously_reported for a in articles):
        errors.append('Selected records include previously reported papers')
    if any(not a.title or not (a.doi or a.url) for a in articles):
        errors.append('Every selected record needs a title and a canonical link')
    for a in articles:
        alerts = a.publication_alerts + a.metadata_verification.get("publication_alerts", [])
        if any(any(term in value.casefold() for term in ("retract", "withdraw")) for value in alerts):
            errors.append(f"Retracted record or retraction notice: {a.doi or a.title}")
        if any("concern" in value.casefold() for value in alerts):
            warnings.append(f"Expression of concern needs editorial review: {a.doi or a.title}")
        if is_notice(a):
            errors.append('Correction/retraction notices do not count toward paper volume')
    if n > int(policy.get('max_articles', 50)):
        errors.append('Selected count exceeds delivery maximum')
    if preprints > min(int(policy.get('max_preprints', 3)), math.floor(n*float(policy.get('max_preprint_fraction', 0.1)))):
        errors.append('Preprint ceiling exceeded')
    if n and published/n < float(policy.get('min_published_fraction', 0.9)):
        errors.append('Fewer than 90% have metadata supporting formal publication; verify uncertain versions')
    if n < int(policy.get('min_articles', 30)):
        warnings.append('Below 30 papers: complete journal/method channels and 14/30-day recovery, or explain the shortfall')
    if n and title_only/n > float(policy.get('title_only_max_fraction', 0.2)):
        warnings.append('Many missing abstracts; recover author abstracts and keep unresolved records in a clearly labelled watchlist')
    if cns < int(policy.get('cns_target_range', [12,18])[0]):
        warnings.append('CNS share below soft target; document journal scan coverage, never fill with irrelevant records')
    return {'ok':not errors,'counts':{'selected':n,'published':published,'preprints':preprints,'unverified':n-published-preprints,'title_only':title_only,'cns':cns},'errors':errors,'warnings':warnings}


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--config',type=Path,default=Path(__file__).resolve().parents[1]/'config/interests.yaml')
    parser.add_argument('--report', type=Path, help='Validate evidence labels and flag unsupported details in the rendered brief')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args(); payload=load_config(args.input)
    records = payload.get('articles', payload.get('candidates', []))
    result = check_selection(records, load_config(args.config))
    if args.report:
        evidence = check_report(records, args.report.read_text(encoding="utf-8"))
        result["report_evidence"] = evidence
        result["errors"].extend(evidence["errors"])
        if evidence["needs_review"]:
            result["warnings"].append(f'{len(evidence["needs_review"])} entries contain details to review against available evidence')
        result["ok"] = not result["errors"]
    encoded=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text(encoded)
    print(encoded,end='')
    return 0 if result['ok'] else 2
if __name__=='__main__':
    raise SystemExit(main())
