# Context refresh commands

Run from the literature-brief project after enabling optional context refresh in a local configuration. Copy the example registry and add the chats or project files the reader has authorized. Codex uses available native chat/project tools and the Python helper compiles evidence-backed interests. Record unavailable tools in the refresh audit.

```bash
SKILL_DIR="$PWD/.agents/skills/ecology-morning-brief"
mkdir -p config
cp -n "$SKILL_DIR/config/ecology-context-sources.example.json" config/ecology-context-sources.json
python3 "$SKILL_DIR/scripts/refresh_context.py" plan --registry config/ecology-context-sources.json --inventory work/ecology-context-inventory-YYYY-MM-DD.json --state-dir .ecology-morning-brief-state --date YYYY-MM-DD --output work/ecology-context-read-plan-YYYY-MM-DD.json
```

The inventory JSON contains `checked_at` (timezone-aware timestamp), `projects`, `threads`, `inventory_limit: 50` and `unavailable_sources`. Prefer returned IDs and host IDs; never guess them. Inspect `thread_reads`, deferred reads, unchanged source IDs and configured files. Read changed/new sources using the native tools, then save a bundle:

```json
{
  "schema_version": 1,
  "checked_at": "2026-11-09T01:00:00+00:00",
  "discovery": {"threads": {"status": "ok", "limit": 50, "complete": false}},
  "sources": [{
    "source_id": "chatgpt:RETURNED_ID",
    "kind": "chatgpt",
    "thread_id": "RETURNED_ID",
    "project_id": "RETURNED_PROJECT_ID",
    "title": "Actual returned title",
    "uri": "https://chatgpt.com/c/RETURNED_ID",
    "updated_at": "2026-11-08T01:00:00+00:00",
    "retrieved_at": "2026-11-09T01:00:00+00:00",
    "status": "ok",
    "coverage": {"has_more": false, "attachments_read": false},
    "content": "Actual user text",
    "messages": [{"turn_id": "RETURNED_TURN_ID", "message_id": "RETURNED_MESSAGE_ID", "role": "user", "timestamp": "2026-11-08T01:00:00+00:00", "text": "Actual user text"}]
  }]
}
```

Use real records, not the illustrative IDs or dates above. For failed/unavailable sources preserve source identity and an error; do not invent empty successful messages. Only the configured exact project files are read by `apply`.

After scientific reading, create decisions, for example:

```json
{
  "upsert_interests": [{
    "id": "research_theme",
    "label": "中文主题",
    "summary": "由实际用户消息支持的研究关注点。",
    "strength": "exploratory",
    "keywords": ["specific scientific phrase"],
    "queries": {"direct": "bibliographic query", "method": "transferable method query"},
    "evidence": [{"source_id": "chatgpt:RETURNED_ID", "turn_id": "RETURNED_TURN_ID", "quote": "Exact excerpt actually present in the user message"}]
  }],
  "deactivate_interests": []
}
```

When no supported scientific change is found, use empty lists. Source access failure must be recorded in the bundle even when the decision lists are empty.

```bash
python3 "$SKILL_DIR/scripts/refresh_context.py" apply --registry config/ecology-context-sources.json --input work/ecology-context-bundle-YYYY-MM-DD.json --decisions work/ecology-context-decisions-YYYY-MM-DD.json --profile work/ecology-search-profile.json --state-dir .ecology-morning-brief-state --date YYYY-MM-DD --output work/ecology-context-audit-YYYY-MM-DD.json --changes-output outputs/context-refresh/YYYY-MM-DD.md
```

This command saves the old profile by content hash under `.ecology-morning-brief-state/context/profile-history/`, appends the full audit to `context/changes.jsonl`, updates `context/state.json`, and replaces the active profile. The JSON audit's sibling `*-sources.json` retains the actual source excerpts and file hashes. Repeated same-day runs retain audit history in JSONL even if the latest dated display file is replaced.

Then run the normal `build_context_config.py`, `build_search_plan.py`, `fetch_candidates.py` and editorial steps. Include refresh metadata and the Chinese change-record link in the brief. This refresh updates the interests used by retrieval; it does not mark any literature reported.

## Additive updates

Submit only additions or supported refinements. Existing topic keywords and query variants are unioned with new ones. Empty decision lists preserve the existing library. `deactivate_interests` is reserved for an explicit user request and requires `user_requested: true`, an exact user quote and the reason. Never deactivate because a topic is old, absent from recent chats or outside today's rotating module budget. Changes record retained/added/removed keywords separately.
