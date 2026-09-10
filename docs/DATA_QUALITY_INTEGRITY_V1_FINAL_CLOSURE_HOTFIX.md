# Data Quality & Integrity V1 — Final Closure Hotfix

This document records the closure scope for the isolated branch
`feat/data-quality-integrity-v1`. It is an implementation and test record, not
permission to merge, push, run extraction, or write the production PRIMARY.

## Closed merge blockers

- Historical wide `product_localizations` HTML/UI findings resolve the row
  `source_hash` before field inspection; the hash is retained on every issue.
- Historical audit and Master Quality use the same deterministic promotion
  classifier. Valid promotion language (`descuento`, `discount`, `rebaja`,
  `oferta`, `promotion`) is accepted; unit-price and process-note leakage is
  rejected; raw `Nuevo`/sustainability badges remain evidence rather than
  promotion contamination.
- Repair findings are routed before candidate creation. Category gaps enter the
  category backlog; identity/orphan findings remain review/archive queues; only
  explicitly executable corrections become candidates.
- Reviewer approval requires `human:<id>`. Apply and verify actors are stored
  separately from reviewer identity (`applied_by/at`, `verified_by/at`).
- Fixture apply is `FIXTURE_APPLIED`, keeps `result_commit_id` null, and cannot
  write a PRIMARY database. Formal preparation emits immutable localization
  patches or an official-fact correction bundle only after approval.
- Master Quality checks localization, provenance, text, category/detail and
  image projections in the `CURRENT` SKU scope. Formal price/event history
  orphan checks remain all-history checks.
- Required collection metrics (`listing_unique`, `current_valid`,
  `successful_category_count`) are fail-closed for daily runs. Non-collection
  correction bundles can explicitly opt out.
- Baselines use calendar observation/run dates, exclude the current day, pick
  the last healthy run per day, and exclude unhealthy runs from 7/30-day
  windows.
- The daily orchestrator evaluates collection quality once before commit; the
  bundle writer validates persisted evidence and does not recalculate it.

## Validation performed

The following checks are required at this head:

```text
python -m compileall -q src       PASS
python -m pytest -q               PASS
CI-safe allowlist                 PASS
git diff --check                  PASS
```

The test suite includes regression coverage for the historical `source_hash`
ordering bug, shared promotion semantics, routing counts/backlog behavior,
reviewer/applier separation, fixture/formal repair separation, CURRENT scope,
calendar baselines, required-metric fail-closed behavior, and pre-commit
collection orchestration.

## Explicit non-goals

No production extraction, real PRIMARY write, dictionary Apply, AI/model call,
profile change, lifecycle rule change, or `main` modification is part of this
hotfix. Merge/push remain separate human-authorized release actions.
