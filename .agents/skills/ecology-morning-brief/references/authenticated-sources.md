# Literature source access

## Source classes

Core daily discovery uses Crossref, sharded Europe PMC and the independent journal-watch adapter (Crossref ISSN works plus Europe PMC journal batches). OpenAlex, Semantic Scholar and Web of Science are boosters; Google Scholar via SerpApi is disabled by default. bioRxiv/arXiv repository scans are disabled by default and supplementary when explicitly enabled. An unavailable booster never discards core results. ScienceDirect is not a complete index and is not a primary adapter.

## OpenAlex

Uses the Works API with search/date filters. Anonymous access is possible; `OPENALEX_API_KEY` adds a dedicated allowance and is sent only as a bearer header. Abstracts are reconstructed only from `abstract_inverted_index`. Citation expansion uses structured DOI lookup, referenced works and `cites` filters. See <https://help.openalex.org/api/>.

## Semantic Scholar

Uses Academic Graph search and citation endpoints. Anonymous access shares rate limits; `SEMANTIC_SCHOLAR_API_KEY` or `S2_API_KEY` is sent only as `x-api-key`. Only the returned abstract field is evidence. See <https://api.semanticscholar.org/api-docs>.

## arXiv/q-bio

Uses the public Atom API for routed method/frontier queries and configured q-bio categories. It is targeted preprint discovery, not a general ecology index. Requests are capped/paced. Returned summaries are abstracts; absent summaries remain title-only. See <https://info.arxiv.org/help/api/index.html>.

## Web of Science

Uses the Clarivate Starter API and requires `WOS_API_KEY` or `WEB_OF_SCIENCE_API_KEY`. The key is sent only as `X-ApiKey`. Records may lack dependable abstracts and then remain title-only. See <https://developer.clarivate.com/apis/wos-starter>.

## Google Scholar

Scholar has no official public search API for this workflow. Do not scrape results. The optional adapter uses SerpApi and `SERPAPI_KEY`; it is disabled by default. Result snippets are discovery metadata, not abstracts. See <https://serpapi.com/google-scholar-api>.

## Failure and security boundary

Missing booster credentials produce `unavailable`; provider errors produce `failed`, or `partial` if records were already collected. Preserve successful sources and never print/persist keys.

Never automate institutional sign-in, reuse browser SID/session data, scrape credentials, bypass CAPTCHA or paywalls, call Sci-Hub, or download restricted full text. Record the limitation and continue.
