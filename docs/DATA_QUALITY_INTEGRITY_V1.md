# Data Quality & Integrity V1

## What this adds

The `action_tracker.data_quality` package provides one issue contract for
historical, current and collection findings. Issue IDs are deterministic
SHA-256 keys over issue type, scope, SKU, run, field and source hash. The
additive SQLite schema contains:

- `data_quality_issues`
- `collection_quality_metrics`
- `repair_batches`
- `repair_candidates`

## Historical repair

`audit-history` detects invalid original prices, promotion/UI/HTML
contamination, unresolved historical identity, orphan price/event history and
missing categories. It uses a SQLite read-only connection and can optionally
persist only the issue records. `repair-build` creates idempotent candidates;
`--output report.json` or `--output report.csv` creates a human-review preview.
No candidate changes facts. Approval, current base commit and source-hash
validation are required before the fixture-only apply adapter can run.

```powershell
python -m action_tracker data-quality audit-history
python -m action_tracker data-quality repair-build --output repair-preview.json
python -m action_tracker data-quality repair-status --batch-id <batch>
python -m action_tracker data-quality repair-approve --candidate-id <candidate> --reviewer <actor>
python -m action_tracker data-quality repair-apply --batch-id <batch> --commit --actor <actor>
python -m action_tracker data-quality repair-verify --batch-id <batch>
```

## Master Quality Gate

`master-quality` is deterministic and read-only. Blocking rules include
duplicate/current-invalid SKU facts, invalid original prices, contaminated
official text, promotion contamination, orphan formal history, missing field
provenance and missing source hashes. Category, description, detail, image and
legacy evidence gaps are visible warnings. A failed result is a prerequisite
failure for the Research Release Gate.

```powershell
python -m action_tracker master-quality --json
```

## Collection Integrity

Collection metrics are persisted per run. Missing source metrics are stored as
`UNAVAILABLE`, never fabricated. Baselines use previous, 7-day median and
30-day median healthy runs; failed, blocked and degraded runs are excluded.
Collection states are separate from `qa_state`:

`COLLECTION_OK`, `COLLECTION_WARN`, `COLLECTION_DEGRADED`,
`COLLECTION_BLOCKED`.

Blocked collections cannot commit. Degraded commits are blocked unless a
bounded one-shot override includes actor, reason, matching run ID, matching
metrics hash and a future expiration timestamp. Schema drift records current,
baseline, delta and sample evidence as data-quality issues.

The SQLite PRIMARY `commit_daily_bundle` path evaluates and persists these
metrics before opening the product commit transaction. A blocked result raises
`COLLECTION_QUALITY_BLOCKED`; the product bundle is not written.

```powershell
python -m action_tracker collection-quality --run-id <run> --json
python -m action_tracker collection-quality history
```

## Production boundary

This V1 feature branch does not run production extraction, does not write the
real SQLite PRIMARY, does not apply historical repair to production, and does
not enable AI, Qwen, localization Apply or auto approval. Merge and push are
deliberate release actions after human review and exact-head CI.
