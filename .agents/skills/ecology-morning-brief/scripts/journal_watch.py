#!/usr/bin/env python3
"""Keyword-independent journal discovery using public publisher-deposited metadata."""
from __future__ import annotations
import copy
from datetime import datetime
from typing import Any
from brief_engine import APIClient, Article, collect_crossref, collect_europepmc, deduplicate, normalize_title


def collect_journal_watch(client: APIClient, config: dict[str, Any], start: datetime, end: datetime) -> tuple[list[Article], bool, str]:
    settings = config['sources']['journal_watch']
    journals = config.get('journal_watch', {}).get('journals', [])
    rows = int(settings.get('rows_per_journal', 60))
    pages = int(settings.get('max_pages_per_journal', 2))
    maximum = int(settings.get('max_articles', 2500))
    values: list[Article] = []
    errors: list[str] = []
    succeeded = 0
    # Each journal has its own budget; a high-volume journal cannot starve later journals.
    for journal in journals:
        run = copy.deepcopy(config)
        source = run['sources']['crossref']
        source.update(endpoint=f"https://api.crossref.org/journals/{journal['issn']}/works", rows_per_query=rows, max_articles=rows)
        run['retrieval_queries'] = ['']
        query_id = f"journal-{journal['issn']}"
        run.setdefault('_search_plan', {}).setdefault('query_lookup', {}).setdefault('crossref', {})[''] = [query_id]
        for page in range(pages):
            source['extra_params'] = {'offset': page*rows}
            try:
                old_config = getattr(client, 'config', None)
                if old_config is not None:
                    client.config = run
                try:
                    records, partial, error = collect_crossref(client, run, start, end)
                finally:
                    if old_config is not None:
                        client.config = old_config
                succeeded += 1
            except RuntimeError as exc:
                errors.append(f"{journal['name']}: {exc}")
                break
            accepted = []
            names = [journal['name']] + journal.get('aliases', [])
            for record in records:
                if not any(normalize_title(record.journal) == normalize_title(name) or
                           (name.startswith('Proceedings of the National Academy') and normalize_title(record.journal).startswith(normalize_title(name)))
                           for name in names):
                    errors.append(f"{journal['name']}: unexpected venue metadata excluded")
                    continue
                accepted.append(record)
                record.sources = sorted(set(record.sources + ['journal_watch']))
                record.discovery_methods = ['journal_watch', 'crossref_issn']
                record.query_ids = [query_id]
            values.extend(accepted)
            if partial or error:
                errors.append(f"{journal['name']}: {error or 'partial'}")
            if len(records) < rows:
                break
            if page+1 == pages:
                errors.append(f"{journal['name']}: journal page budget reached")
    # Independent journal queries in Europe PMC recover author abstracts that Crossref lacks.
    batch_size = max(1, int(settings.get('epmc_batch_size', 6)))
    for index in range(0, len(journals), batch_size):
        batch = journals[index:index+batch_size]
        run = copy.deepcopy(config)
        query = '(' + ' OR '.join(f"ISSN:{journal['issn']}" for journal in batch) + ')'
        run['europepmc_queries'] = [query]
        cap = int(settings.get('epmc_max_articles_per_batch', 180))
        run['sources']['europepmc'].update(max_articles=cap, max_articles_per_query=cap,
                                          max_pages_per_query=4, published_only=True)
        run.setdefault('_search_plan', {}).setdefault('query_lookup', {}).setdefault('europepmc', {})[query.casefold()] = [f'journal-batch-{index//batch_size+1}']
        try:
            old_config = getattr(client, 'config', None)
            if old_config is not None:
                client.config = run
            try:
                records, partial, error = collect_europepmc(client, run, start, end)
            finally:
                if old_config is not None:
                    client.config = old_config
            succeeded += 1
        except RuntimeError as exc:
            errors.append(f'Europe PMC journal batch: {exc}')
            continue
        for record in records:
            record.sources = sorted(set(record.sources + ['journal_watch']))
            record.discovery_methods = ['journal_watch', 'europepmc_journal']
        values.extend(records)
        if partial or error:
            errors.append(error or 'Europe PMC journal batch capped')
    if not succeeded and errors:
        raise RuntimeError('; '.join(errors[:3]))
    unique = deduplicate(values, float(config.get('title_similarity_threshold', 0.94)))
    if len(unique) > maximum:
        # Preserve journal diversity if the global safety ceiling is reached.
        groups: dict[str, list[Article]] = {}
        for record in unique:
            groups.setdefault(record.journal, []).append(record)
        diversified = []
        while groups and len(diversified) < maximum:
            for journal in list(groups):
                diversified.append(groups[journal].pop(0))
                if not groups[journal]:
                    del groups[journal]
                if len(diversified) == maximum:
                    break
        unique = diversified
        errors.append('journal-watch global record budget reached')
    return unique, bool(errors), '; '.join(dict.fromkeys(errors))
