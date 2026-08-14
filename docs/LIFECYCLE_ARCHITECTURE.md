# Action SKU Presence and Lifecycle Architecture

Updated: 2026-08-12

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

- The Presence gate is frozen after sitemap and all primary category listings, before Detail enrichment starts.
- An invalid sitemap, or incomplete primary category coverage without a valid sitemap fallback, fails QA.
- If a valid sitemap has already been frozen but a later primary Listing scan becomes incomplete or restricted, the run may be `PASS_PRESENCE_ONLY`: CURRENT and lifecycle use Sitemap Presence, while unobserved Listing fields remain baseline/pending. A missing sitemap is never eligible for this fallback.
- Detail is non-authoritative enrichment. A later Detail restriction is recorded as `DETAIL_ACCESS_INTERRUPTED`/`ACCESS_INTERRUPTED`; it does not invalidate already-complete Presence evidence and never changes lifecycle by itself.
- On the first Detail access restriction, the controller performs its configured cooldown and permits exactly one cautious probe of the same SKU. A successful probe returns to `NORMAL`; a second restriction becomes `BLOCKED` and stops the remaining Detail queue. This does not reload 401/403/429 responses or bypass a challenge.
- Snapshot product rows explicitly record `presence_source`, `listing_fields_source`, `detail_fields_source`, and `detail_status` so carried-forward or pending Detail data is not presented as freshly collected.
- QA failure and dry-run may save evidence, staging, and reports, but must not update Master, `known_skus.csv`, or `offline_skus.csv`.
- A non-dry run can commit only after the authoritative Presence QA passes. Detail completion is reported independently and can be continued with `detail-retry`.

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
