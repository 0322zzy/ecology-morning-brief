---
name: ecology-morning-brief
description: Discover ecology, life-science, CNS-family and transferable-method papers; create broad Chinese daily literature briefs of 30–50 papers with published-version priority, evidence labels and history deduplication. Use for ecology literature monitoring, recent-paper searches, deep topic searches, weekly catch-up, user-requested journal additions, and updates to this workflow. Do not use for general news monitoring.
---

# Ecology Morning Brief

Run auditable literature discovery inside the current Codex turn. Scripts retrieve, normalize, deduplicate and preserve discovery channels; Codex reads the available evidence, makes scientific selections and writes Chinese summaries. Do not call an external summarization model or imply access to unread full text.

## Current daily brief contract

- Deliver **30–50 distinct papers**, targeting **40**: about **8 detailed readings** plus concise, substantive summaries for the rest. If fewer are available, run the recovery procedure before explaining the shortfall. Never pad with duplicates or unsupported findings.
- Prioritize formal publications: at least **90% published**, preprints **at most 3 and at most 10%**, clearly separated. bioRxiv/arXiv scanning is disabled by default. A repository/index is not a publication venue; check version metadata even for Europe PMC records.
- Independently browse the configured journals (**28 by default**), including Nature/Science/Cell and selected family journals, PNAS and ecology journals. Aim for roughly **12–18 CNS-family papers** and **10–15 method/idea papers** when useful supply permits. These overlapping soft targets express reading preferences, not scientific quality scores or quotas.
- **Accumulate interests and keywords.** Refresh by adding and refining the existing library; preserve earlier topics, terms and query variants. A quiet period, age of evidence or daily query rotation must not erase them. Deactivate only on an explicit user request, recording the reason and retained history.
- Include thermal ecology, demography, conservation and forecasting among the starter themes and adapt them to the reader’s stated interests, while actively including broader life science, evolution, communities, Earth systems, statistics, AI and experimental methods. Weak immediate project relevance is acceptable. Explain an interesting method, mechanism or question without inventing a connection to the user's current species or dataset.

Read [references/editorial-policy.md](references/editorial-policy.md) for selection and writing. Config values are authoritative; current explicit user instructions override them.

## Modes and retrieval

- `daily`: default, 7-day rolling publication window; journal browsing plus topical and cross-disciplinary method queries. Expand to **14 days, then 30 days** if fewer than 30 suitable unseen papers remain. Label older recovery papers with their actual dates. History exclusion runs in every window.
- `weekly-deep`: 21 days, broader query coverage and bounded one-hop citation expansion from up to four seeds.
- `topic-deep`: a specified historical/narrow question, bounded expansion from up to six seeds. Use profile `retrieval_mode: focus` when requested; it disables unrelated journal browsing. The daily volume/mix targets apply to daily briefs, not a narrow topical bibliography.

## Workflow

1. **Determine the run scope.** Use the reader’s current request and the selected configuration. Prefer an explicitly supplied config, then project-local `config/interests.local.yaml` if present, then the bundled `config/interests.yaml`. Apply requested journal additions using the procedure below. If `context_refresh.enabled` is true in the chosen config, refresh the reader’s explicitly configured chats and project files before daily retrieval. Follow [context adaptation](references/context-adaptation.md) and its [commands](references/context-refresh-cli.md). Record unavailable or degraded access. Use only the scope authorized by the reader.
2. Run `scripts/build_search_plan.py`, then `scripts/fetch_candidates.py` with the selected base or effective config and the **project-local state directory**. For an opted-in context refresh, use `scripts/build_context_config.py` first. See [retrieval commands](references/retrieval-v2.md) for the default and personalized workflows. The daily candidate budget is 600.
3. Inspect source states and candidate composition. Journal browsing must run independently of keyword matches. Crossref and sharded Europe PMC are core; OpenAlex/Semantic Scholar/WoS/Scholar are optional boosters. Failures must preserve successful records. If a core channel fails, attempt its independent counterpart or bounded official publisher-page discovery; disclose remaining gaps. Never bypass access controls.
4. Read abstracts and select across CNS/frontier, methods/ideas, core interests and broader science. Rule scores are aids, not a requirement for every lane. In deep modes, run `scripts/expand_citations.py` when enabled.
5. Verify finalists with `scripts/verify_metadata.py`. Then run `scripts/check_selection.py` on the **complete selected records**. Resolve errors; review soft-target warnings. Missing Crossref metadata alone does not delete a paper, but an uncertain publication version cannot be counted as verified published.
6. Recompute `scripts/evaluate_coverage.py` with selected records. Report **operational search saturation** only. Source failures, caps or unattempted queries limit coverage even when yield appears to plateau.
7. Produce the full Chinese brief and a compact retrieval audit. Save `outputs/ecology-morning-brief/YYYY-MM-DD.md` and `latest.md`. If a report for that day already exists, retain a dated backup before replacing it.
8. Only after the report is successfully generated and delivered, run `scripts/mark_reported.py` on the papers actually included. Do not mark fetched, rejected or undelivered candidates.

## Add journals on request

If the reader names journals, or asks to add journals covering a topic, identify the relevant titles and verify each official title and ISSN with Crossref, the publisher, or an authoritative journal catalogue. Use the index title or pass verified variants as aliases after the ISSN. Leave ambiguous or unverified matches pending and report them.

Run `scripts/extend_journal_watch.py --base PATH --journal "Verified title" ISSN --output PATH` from the skill directory or use its full path. Repeat `--journal` for multiple journals. This appends unique entries to the independent journal watch and preserves its default journals. Use `work/ecology-journals-config.json` for a single-run output. Save to project-local `config/interests.local.yaml` only when the reader asks to retain the additions. If that local config already exists, use it as the base for persistent updates and preserve its other preferences.

Apply additions before the context merge, then use the resulting config in planning, retrieval, every recovery window, and editorial checks. Daily and weekly-deep briefs in extend mode browse these journals alongside the default set using Crossref ISSN works and Europe PMC. Report newly added and unresolved journals and any source failures. When the request only changes the saved list, return the configuration result without generating a brief.

## Evidence and output

Use only returned titles, author abstracts and metadata unless full text was read. Every paper gets `abstract-supported` or `title-only`; for the latter display `摘要不可用，以下判断仅基于标题和元数据。` Never convert a title into an asserted result. Scholar snippets are not abstracts. Prefer a matched published version, preserving related preprint identifiers for deduplication.

Use Chinese sections such as `今日重点`, `CNS 与前沿发现`, `方法与 idea`, `生态学与全球变化`, `预印本观察`; omit empty sections and count a paper once. Every entry has a canonical link, journal/date/authors, publication status, evidence level, a substantive cautious takeaway and an idea/reading reason. A forced project connection is unnecessary.

When context refresh is enabled, add a short `今日检索兴趣变化` paragraph linking `outputs/context-refresh/YYYY-MM-DD.md`, with actual added/updated/unchanged interests, source names and access limits. Preserve the CNS/broad-method lanes as context changes.

Audit: if enabled, context refresh time/status, web/Codex/project source counts, interest changes and coverage gaps; searched/deduplicated/candidate/selected, published/preprint/unverified mix, CNS and method/idea counts, title-only count, windows/recovery, attempted queries, source states/caps and stop reason. Do not let the audit dominate the brief.

For persistent changes read [references/configuration.md](references/configuration.md); for source access read [references/authenticated-sources.md](references/authenticated-sources.md); for graph expansion read [references/coverage-and-expansion.md](references/coverage-and-expansion.md).

For recurring delivery, invoke `$ecology-morning-brief` in an automation configured by the reader, and require pre-search context refresh only if enabled. Keep quiet when there are no suitable unseen papers unless empty reports were requested; surface failures that need user action.
