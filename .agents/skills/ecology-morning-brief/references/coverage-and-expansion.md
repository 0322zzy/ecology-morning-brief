# Coverage and citation expansion

## Operational coverage

`evaluate_coverage.py` uses record provenance, not an unknowable global denominator. It reports per-query searched/unique/relevant/selected/marginal gains; per-source returned/unique-only/overlap/relevant/selected contribution; and totals plus a low/moderate/high `operational search saturation` level.

Request logs record successful zero-result queries and failed requests separately. Failures, page/record caps and unattempted routed core queries prevent a high-coverage interpretation. Only successfully attempted queries affect the trailing low-gain streak. Unrouted zero rows cannot fabricate high saturation. The level is a soft stop signal and never proves completeness. Low daily saturation is acceptable for bounded monitoring; in deep modes it may justify one expansion round, a booster, a narrower reformulation or a limitation note.

## Citation expansion

- daily: off;
- weekly-deep: at most 4 seeds × 20 records, one hop;
- topic-deep: at most 6 seeds × 30 records, one hop.

Seeds must have a DOI and be direct/adjacent/method-transfer records from citation-enabled queries. Review records are useful seeds when scientifically relevant. OpenAlex and Semantic Scholar retrieve backward/forward neighbors; failures remain isolated.

Neighbors receive direction and seed provenance, then pass through normalization, study-level dedup, scoring, classification and history checks. Citation proximity is discovery evidence, not relevance or quality evidence.

Valid stop reasons include daily budget, one-hop limit, source page/query cap, consecutive low marginal gain, exhausted eligible seeds, unavailable booster or satisfied user scope. “All literature found” is never a valid stop reason.
