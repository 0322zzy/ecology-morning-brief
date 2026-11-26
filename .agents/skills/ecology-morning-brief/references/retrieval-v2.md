# Retrieval CLI and recovery

Run from the project directory. Point `SKILL_DIR` at the skill folder if you installed it somewhere else. Generated files and history belong to the project where you run the brief.

```bash
SKILL_DIR="$PWD/.agents/skills/ecology-morning-brief"
BASE_CONFIG="$SKILL_DIR/config/interests.yaml"
python3 "$SKILL_DIR/scripts/build_search_plan.py" --config "$BASE_CONFIG" --mode daily --output work/ecology-search-plan.json
python3 "$SKILL_DIR/scripts/fetch_candidates.py" --config "$BASE_CONFIG" --plan work/ecology-search-plan.json --state-dir .ecology-morning-brief-state --output work/ecology-candidates.json --coverage-output work/ecology-coverage-report.json
```

The plan defaults to the current date in `report_timezone`. Pass `--date YYYY-MM-DD` to select a different report day.

For optional personalization, set `context_refresh.enabled` to `true` in a local copy of the base config and complete a [context refresh](context-refresh-cli.md). Then merge the same config with the refreshed profile. Use the resulting effective config in all subsequent commands and pass `--profile work/ecology-search-profile.json` to both planning and retrieval.

```bash
BASE_CONFIG=config/interests.local.yaml
python3 "$SKILL_DIR/scripts/build_context_config.py" --base "$BASE_CONFIG" --profile work/ecology-search-profile.json --output work/ecology-effective-config.json
BASE_CONFIG=work/ecology-effective-config.json
```

Inspect the plan's module diversity, `source_route`, per-source states and publication/channel counts. `journal_watch` browses every configured ISSN through Crossref and independent Europe PMC journal batches, without topical keywords. It preserves successful journal/provider results when others fail. `europepmc` uses independent query shards, fair per-query capacity, publication-date sorting and published-only filters. OpenAlex/Semantic Scholar/WoS are conditional boosters; `--enable-boosters` forces eligible routes without bypassing credentials. `--source` is repeatable for explicit restrictions or bounded validation. A `partial` result means incomplete retrieval, not exhaustion.

## Recover a daily shortfall

If editorial review leaves fewer than 30, complete failed/unattempted journal/method channels and build a separate plan with `--lookback 336`, then `--lookback 720` if needed. Use distinct filenames (for example `ecology-search-plan-14d.json`, `ecology-candidates-14d.json`) and the same state directory. Merge candidates by `Article` normalization plus `deduplicate`, preserving query/source/related DOI provenance. Re-check history and scientific suitability; keep the actual publication dates. Do not merely lower the threshold repeatedly or substitute preprints. The report must disclose the windows actually used and the resulting shortfall if any.

In deep modes, run `expand_citations.py` with the effective config; returned neighbors must be normalized, deduplicated, screened and history-checked. Daily expansion is disabled.

## Verify and check the selected mix

Save the full selected Article records under `articles`, including abstract, journal, identifiers, version/type, provenance and evidence fields. Do not reduce selections to title/DOI pairs before the selection check.

```bash
python3 "$SKILL_DIR/scripts/verify_metadata.py" --input work/ecology-selected.json --config "$BASE_CONFIG" --output work/ecology-selected-verified.json
python3 "$SKILL_DIR/scripts/check_selection.py" --input work/ecology-selected-verified.json --config "$BASE_CONFIG" --output work/ecology-selection-check.json
python3 "$SKILL_DIR/scripts/evaluate_coverage.py" --input work/ecology-candidates.json --config "$BASE_CONFIG" --selected work/ecology-selected-verified.json --output work/ecology-coverage-report.json
```

Resolve version/duplicate/link/ceiling errors. Warnings concern soft targets, evidence gaps or a volume shortfall and require editorial explanation. Verification errors retain records for review; they never turn an unknown version into a published one. If multiple retrieval passes contributed papers, audit each pass or merge its audit provenance as well; do not describe just the initial window as complete coverage.

Write and deliver the Chinese report, then:

```bash
python3 "$SKILL_DIR/scripts/mark_reported.py" --input work/ecology-selected-verified.json --state-dir .ecology-morning-brief-state
```

Only records actually included in the delivered report may be marked.

## API design references

- [Crossref journal works and date/type filters](https://www.crossref.org/documentation/retrieve-metadata/rest-api/tips-for-using-the-crossref-rest-api/)
- [Europe PMC REST search, core abstracts and sorting](https://dev.europepmc.org/RestfulWebService)

These support structured metadata discovery, not exhaustive coverage or full-text access.
