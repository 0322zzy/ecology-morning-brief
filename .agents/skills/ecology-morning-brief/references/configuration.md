# Configuration and state

`config/interests.yaml` is JSON-compatible YAML, loaded without dependencies. Keep personal preferences in a project-local copy and run-specific interests in a profile. Pass the chosen config explicitly to the scripts.

## Current daily defaults

| Setting | Value |
|---|---|
| Output | 30–50, target 40; 8 detailed entries |
| Publication mix | ≥90% formal publications; preprints ≤3 and ≤10% |
| Soft browsing targets | 12–18 CNS-family; 10–15 methods/ideas; overlap allowed |
| Window | 168 h; recover at 336 and 720 h if needed |
| Candidate pool | 600; rule threshold 12, independent journal/method lanes |
| Query budget | ≤12 modules × 4 query types, plus bounded exploration |
| Core discovery | Crossref, sharded Europe PMC, 28-journal watch |
| Boosters | OpenAlex, Semantic Scholar, WoS; credentialed Scholar disabled |
| Repository scans | bioRxiv/arXiv disabled by default |
| Serendipity candidate ceiling | 35%; not the total broad-reading share |

`delivery` defines output/mix limits, writing depth and recovery windows. `journal_watch.journals` records journal names, ISSNs and groups (`cns`, `leading`, `ecology`); names/groups are preferences rather than quality labels. Watch is active in daily/weekly-deep extend mode and disabled in explicit focus mode. Source settings bound per-journal pages and Europe PMC batches. Log caps as `partial`.

`search.modes` sets query/window/citation budgets. Daily has no citation expansion. Weekly uses 21 days, up to 10 modules and 4 seeds ×20 neighbors; topic-deep defaults to 10 years, 4 modules and 6 seeds ×30 neighbors; both stay at one hop. Narrow topic requests can override historical scope and daily volume targets.

`search.modules` holds scientific concepts and source preferences. Optional `queries` maps query types to exact source-neutral query strings, useful for cross-field methods. Otherwise the planner combines only one or two concept phrases per query. `sources` sets enablement, `core`/`booster` classes, providers and limits. `primary_topics`, supporting/negative/domain language and scoring aid discovery; they do not veto independent editorial lanes.

`europepmc_queries` is generated from routed queries, each with a separate fair result budget and provenance. A legacy `europepmc_query` remains compatible for direct API calls. `sources.europepmc.published_only` excludes PPR/preprint records; record-level detection remains necessary.

Run-scoped profiles never carry credentials or change operational limits. Explicit current scope outranks an old narrow profile. API keys stay in environment variables. Never print/persist credentials.

## History

Always pass `--state-dir .ecology-morning-brief-state` for a working project (or set `ECOLOGY_BRIEF_HOME`). Reuse the existing database, including for recovery. Retrieval updates seen metadata only; `mark_reported.py` records delivered DOI/title/related identities after successful delivery. Back up an existing daily report before revision. Validation uses a separate temporary state, never production history.

## Context refresh state and scope

Context refresh is off by default. Set `context_refresh.enabled` to `true` in a local config to use a 36-hour freshness check and up to four dynamic modules alongside persistent modules. Start from the [empty source registry](../config/ecology-context-sources.example.json). The project-local `config/ecology-context-sources.json` stores selected research project IDs, tracked ChatGPT/Codex chat IDs, exact local document paths, discovery/exclusion terms and read budgets. It is the editable source scope; it contains no credentials.

`.ecology-morning-brief-state/context/state.json` retains interests and checked source timestamps/hashes. `context/changes.jsonl` preserves every refresh audit; `context/profile-history/` preserves previous profiles. `context_modules` and `_context_refresh` in the generated profile are copied into the effective config/plan. Source outages preserve supported interests and are visibly recorded. Interests and keywords accumulate by default; `allow_automatic_expiry: false` prevents age-based removal. Legacy expiry dates are evidence-review metadata under this policy. Old and new terms/query variants are merged. Only a user-requested deactivation may remove a theme from active retrieval, with an audit; its history remains. Daily module limits bound query execution, not the size of the retained keyword library.

The connector's recent inventory is bounded at 50; tracked IDs survive outside that list. Add older important chat IDs to `tracked_threads` to include them explicitly. Read [context refresh](context-refresh-cli.md) for inputs, commands and source evidence.
