# Editorial policy: broad discovery, published first

## Selection

The daily goal is 30–50 papers, normally 40. Preserve several independent routes into the brief:

- CNS/frontier: Nature, Science, Cell and configured family journals. A new biological mechanism, conceptual advance, dataset or reusable method is enough; direct relevance to the current project is optional.
- Methods/ideas: statistics, causal inference, simulation-based inference, probabilistic modelling, forecasting, uncertainty, AI, remote sensing, experimental design and reproducible tools. A method validated in another field can qualify without ecology keywords. Explain what transfers and any assumptions; do not claim the transfer has been validated.
- Core interests: thermal physiology, life stages, time-dependent tolerance, plasticity, demography, spatial/individual models, conservation and eco-evolutionary response.
- Broader science: communities, biodiversity, ecosystems, evolution, Earth systems, climate extremes and sustainability. Include interesting questions and conflicting results, not only positive breakthroughs.

At 40 papers, roughly 12–18 CNS-family entries and 10–15 methods/idea entries are useful soft targets; categories overlap and a paper appears once. Read the abstract for a concrete contribution or plausible idea. Journal brand is a user-requested browsing priority, not proof of novelty, reliability or relevance. Avoid repetitive examples of the same incremental idea. Clinical or engineering studies may enter via a useful general method; generic acronym matches and unrelated applications remain poor candidates. Corrections, obituaries, news and retraction notices do not fill the paper target. Reviews and substantial scientific Perspectives may qualify, clearly labelled.

Use transparent lanes `direct_hit`, `adjacent_topic`, `method_transfer`, `broader_ecology`, `serendipity`. Codex may correct rule classifications after reading abstracts. Do not reject journal-watch or cross-field candidates solely for a low topical keyword score. The 35% serendipity setting bounds that candidate lane, not all broadly interesting CNS articles.

## Publication mix

At least 90% of the daily selection must have metadata supporting formal publication; preprints are optional, at most 3 and at most 10% (use the stricter cap). Separate them in `预印本观察` and label peer-review status as unreviewed/uncertain as appropriate. Default repository scans are off. Do not replace a failed journal source by flooding the report with preprints. Europe PMC, OpenAlex and Scholar contain multiple publication types; check each paper, including repository venues, source PPR, publication type, DOI and matched formal version.

When a published/preprint pair is supported, use the published DOI and retain the preprint DOI in related identities. If version or date remains uncertain, flag `unverified` and resolve before counting it as published. DOI registration alone does not prove peer review.

## Evidence and writing

Use only author-written abstracts, titles and metadata unless full text was actually read. Missing abstracts display exactly:

> 摘要不可用，以下判断仅基于标题和元数据。

Keep title-only entries brief and tentative, preferably in a watchlist; try to keep them under 20% and attempt author-abstract recovery for important finalists. A search snippet is not an abstract. Separate author-reported findings from your proposed idea, limitations and transfer hypothesis. Describe a concrete contribution without automatically calling it innovative.

About 8 spotlight papers receive 180–300 Chinese characters of interpretation each; remaining papers receive about 70–140 characters of substantive synthesis each. These are writing guides, not rigid limits. Every entry includes linked title, first author/et al. (full names retained in JSON), journal, actual date, article type, publication status and evidence level. Give the supported main point plus why to read it or a possible idea. A one-line title/link alone is not a literature summary. Do not force a project connection; `值得关注的问题/方法` is sufficient.

## Volume recovery and audit

Start with 7 days. If fewer than 30 scientifically suitable unseen papers remain, complete the journal and method channels, retry/fallback failed core channels, then use 14- and 30-day plans. Deduplicate the union and exclude reported identities in every pass. Save each recovery plan and result separately. Do not silently extend beyond 30 days; label recovery material with its actual date. If still below 30, deliver the available supported papers and state the shortfall and failed/capped channels. Never invent, repeat or admit unhelpful papers simply to reach a number.

Run `check_selection.py` after verification, before writing the final report. Its errors concern duplicates, links, versions and ceilings; soft-target warnings invite editorial review. It does not judge scientific merit. Report actual mix, windows, source status and limits without claiming complete coverage.
