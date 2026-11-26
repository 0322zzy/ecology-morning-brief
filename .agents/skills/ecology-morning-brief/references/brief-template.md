# Report and evidence template

Write every selected paper as a Markdown heading with the exact canonical link in the selected JSON, followed by one metadata paragraph and one interpretation paragraph. Use this structure for spotlight and concise entries.

```markdown
## 今日重点

### 01. [原始论文题名](canonical URL)

作者｜期刊｜核实的发表日期｜正式发表｜[证据：作者摘要]

在作者摘要支持的范围内说明研究问题、方法和发现。另用「可借鉴」或「值得阅读」标明你的解释或建议。
```

Choose exactly one label per entry: `[证据：作者摘要]`, `[证据：标题+元数据]`, or `[证据：开放全文]`. For title and metadata, say `摘要不可用，以下判断仅基于标题和元数据。` and limit claims to what is known. For open full text, add the exact URL, section or page, and a supporting passage to the selected record as `full_text_evidence: {url, location, excerpt}`, and give the location in the brief. Index snippets and AI-generated highlights do not qualify as author abstracts. Use the source's abstract only when it matches the cited version.

## 检索与质量审计

Give source statuses, counts, coverage limits and recovery choices in a compact paragraph or short list. Link the run's request-audit JSON and final selection check. Include the snapshot SHA-256 (short prefix acceptable when the linked JSON stores the full value). The audit captures request parameters and attempts at collection time. Keep candidate and selected JSON per date/window so later runs can inspect or replay downstream selection with the captured records. Values from multiple sources may overlap.

Run `check_selection.py --report PATH` with the final selected JSON before delivery. Resolve missing or invalid labels and report/selection mismatches. Inspect each `需复核` flag against the stored abstract or the actual read passage. Correct unsupported statements and retain an explanation only for verified false positives, such as a unit conversion. Numeric and figure checks are heuristic.
