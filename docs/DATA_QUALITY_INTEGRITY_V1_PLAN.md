# Data Quality & Integrity V1 Plan

## Scope

This plan adds three independent reliability layers on top of SQLite PRIMARY:

1. Historical Master Repair — discover historical defects and create reviewed,
   auditable candidates.
2. Master Quality Gate — read-only release readiness for the current formal
   SQLite head.
3. Collection Integrity — per-run metrics, healthy baselines, commit gates and
   schema-drift evidence.

SQLite PRIMARY remains the only formal fact source. Excel and CSV remain
generated projections. The feature branch does not write the real PRIMARY,
run a real daily collection, enable AI, or enable automatic approval.

## Delivery phases

| Phase | Deliverable | Gate |
|---|---|---|
| A | Contracts, configuration, additive schema and repository | fixture schema and contract tests pass |
| B | Historical issue audit and stable IDs | repeated audit is idempotent |
| C | Repair batches, review states, preview artifacts, guarded apply adapter and verification | approval, base commit and source hash are required |
| D | Master Quality Gate and Research Release prerequisite | dirty fixture blocks; clean fixture is release-ready |
| E | Collection metrics and 7/30-day healthy baselines | unavailable metrics remain explicit |
| F | Collection states, commit blocking, bounded override and schema drift | damaged collection is blocked |
| G | Full regression, CI-safe allowlist and exact-head CI review | no production writes |

## Safety contract

The implementation must not rewrite Presence, Lifecycle, AccessController,
Cloudflare handling, BrowserSession or the existing immutable correction
contracts. Historical repair candidates are not formal facts. A fixture-only
apply adapter is retained solely to exercise approval and verification in
tests; a PRIMARY database is rejected unconditionally.

## Acceptance evidence

Acceptance is based on synthetic fixtures and read-only inspection. The final
report records the branch, base SHA, head SHA, test totals, CI results and the
fact that `real_primary_written = NO`.

