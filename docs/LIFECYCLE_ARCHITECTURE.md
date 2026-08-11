# Action SKU Presence and Lifecycle Architecture

Updated: 2026-08-11

## Scope

This program is a local **SKU Presence -> Lifecycle Action** monitor for Action Spain.
Daily SKU totals are QA anomaly signals only. They are not lifecycle rules and no fixed count is treated as a target.

## Authoritative data and state

- Official product evidence: Action sitemap and category listing scans; Nuevo and Promocion pages are supplementary badge/presence evidence.
- Cross-day lifecycle state: `runtime/state/known_skus.csv`.
- Derived offline view: `runtime/state/offline_skus.csv`, regenerated from `known_skus.csv[last_status=OFFLINE]`.
- Daily baseline/export: `runtime/master/Action_Master.xlsx`.
- SQLite code under `src/action_tracker/database/` is intentionally frozen scaffolding. It is not read or written by the daily run.
- No translation API/provider is configured. Existing Chinese is retained; missing Chinese falls back to Spanish and is marked `FALLBACK_ES`. Records with retained Chinese but no provider status are `NOT_CONFIGURED`.

## Presence and lifecycle rules

- A SKU is present when observed by sitemap, listing, or the supplementary badge entry points. Evidence is retained with source flags.
- `NEW` means present today and absent from historical `known_skus.csv`.
- `REAPPEARED` means present today, historically known, and absent from the previous CURRENT baseline.
- Therefore `FIRST_SEEN` and `REAPPEARED` are mutually exclusive.
- Absence becomes `MISSING_FIRST`, then `MISSING_CONTINUED`, and becomes `OFFLINE` only after the configured number of valid missing observations.
- An absence is actionable only when sitemap observation is valid, or when the SKU's relevant category listing was completely observed.
- Incomplete collection returns `UNKNOWN`; it never increments `missing_count` and never emits an offline event.
- Re-running the same observation date does not advance missing count or duplicate lifecycle state.

## QA and write gate

- Any blocked fetch or incomplete primary observation fails QA.
- QA failure and dry-run may save evidence, staging, and reports, but must not update Master, `known_skus.csv`, or `offline_skus.csv`.
- A non-dry run can commit only after QA PASS.

## Evidence layout

Each run uses one ID and writes:

```
runtime/snapshots/YYYY-MM-DD/<run_id>/
runtime/staging/<run_id>/
```

Snapshots retain sitemap/listing raw evidence, normalized records, presence evidence, coverage, QA, and run report. Staging retains presence, lifecycle, price, translation, event, and product-change records for the run.

## Validation

Current automated suite: `python -m pytest -q`.
It covers lifecycle transitions, same-day idempotency, invalid observation protection, QA blocking, translation fallback/no-provider state, and state-file persistence.
