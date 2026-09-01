# Localization Knowledge Feed V1

## Scope

This branch adds a read-only first-pass Knowledge Feed. It aggregates
deterministic learning evidence into reusable candidates for human review. It
does not translate, call Qwen, promote knowledge, write a formal dictionary,
or modify SQLite PRIMARY.

## Isolation and provenance

- Branch: `feat/localization-knowledge-growth-v1`
- Worktree: `F:\ActionSKUTracker_worktrees\localization-feed`
- Source database was opened `mode=ro` and copied with the SQLite Backup API.
- Snapshot: `runtime/feed/db/action_tracker_feed_snapshot.db`
- Snapshot SHA-256: `9a7453c63ecad9599386e000578bc056fbb92c5ad8bfe782bd25be667653621d`
- Snapshot source commit: `2026-08-31_2026-08-31_035941_114261d50c75`
- Snapshot integrity: `ok`; foreign-key violations: `0`
- Snapshot CURRENT count: `5,379`
- No website, browser, scheduler, image job, GPU, Ollama, external API, or
  production writer was started.

The audit and all candidate mining read only this snapshot. Reports are under
`runtime/feed/reports/knowledge-feed-v1-baseline/`; generated Feed artifacts
are under `runtime/feed/output/`.

## Candidate contract

The first pass handles only `PRODUCT_TYPE`, `PHRASE`, `TERM`, `DETAIL_KEY`, and
`TECH_TOKEN`. Every emitted candidate includes a stable candidate ID, a
normalized source term, proposed Chinese value, category context when present,
affected SKU counts, per-SKU evidence with source hashes/run/commit IDs, and
conflict/review flags. Evidence is accepted only for CURRENT SKUs whose source
hash is fresh and whose audit row is not source-blocked or hash-mismatched.

Existing formal knowledge is marked `EXISTING_KNOWLEDGE` and is not added to a
new review pool. Conflicting values for the same normalized source are kept as
`EVIDENCE_CONFLICT`; no majority vote or automatic promotion is performed.

Priority is deterministic: affected SKU count descending, evidence SKU count
descending, knowledge type priority, then normalized source.

## Baseline and first Feed result

Run ID: `knowledge-feed-v1-baseline`

| Metric | Value |
|---|---:|
| CURRENT | 5,379 |
| REVIEW_REQUIRED | 4,988 |
| Total candidates | 83 |
| PRODUCT_TYPE | 20 |
| PHRASE | 0 |
| TERM | 0 |
| DETAIL_KEY | 8 |
| TECH_TOKEN | 55 |
| Existing knowledge skipped | 10 |
| Manual conflicts | 0 |
| Evidence conflicts | 0 |
| Candidates marked `needs_ai` | 73 |
| Estimated unique affected SKUs | 5,151 |
| AI calls | 0 |

Top candidates are mostly high-impact detail keys and technical tokens. The
complete ranked list is in `knowledge_feed_top_200.csv`; the SKU-level impact
mapping is in `knowledge_feed_impact.csv`.

## Artifacts

- `runtime/feed/output/knowledge_feed_candidates.csv`
- `runtime/feed/output/knowledge_feed_top_200.csv`
- `runtime/feed/output/knowledge_feed_impact.csv`
- `runtime/feed/output/knowledge_feed_summary.json`
- `runtime/feed/db/snapshot_manifest.json`

## Dictionary and production safety

SHA-256 hashes for all seven formal dictionary inputs (product type, phrase,
term, detail key, technical token, manual overrides, product dictionary) are
identical before and after the run. `dictionary_unchanged=true` is recorded in
the summary. Production Apply, Auto Approval, and AI remain disabled. Candidate
files are review-only and are not formal dictionary contents.

## Tests

`tests/test_localization_knowledge_feed.py` covers aggregation, per-SKU
evidence, existing-knowledge exclusion, conflict handling, priority, hash
guards, and the no-AI contract. The targeted local suite passed 47 tests
(Feed + Localization Intelligence + Final Field Contract Hotfix). The full
CI-safe suite is delegated to GitHub Actions on the pushed branch.

## Stop point

The next stage is human review of the ranked candidates. Qwen enrichment,
manual approval, promotion, dictionary growth, and production Apply are
separate explicitly authorized stages and are not part of Knowledge Feed V1.
