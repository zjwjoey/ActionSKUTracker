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
  separately from reviewer identity (`applied_by/at`, `verified_by/at`). The
  fixture applier is independently validated as `human:<id>` and every
  approved candidate must have a non-empty reviewer different from the apply
  actor; the complete batch fails closed before mutation otherwise.
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
  windows. Persisted `__collection_state` evidence records uppercase
  `qa_state` and `baseline_eligible`; QA FAIL and ineligible runs cannot enter
  a baseline while legacy rows without these fields remain compatible.
- Collection metric hashes are deterministic semantic snapshots: numeric
  values are normalized across SQLite round-trips and persistence metadata is
  excluded. The production writer verifies required metrics, exactly one
  current state marker, marker hash, and a recomputed persisted hash inside
  `BEGIN IMMEDIATE`; it never recalculates collection quality.
- The daily orchestrator evaluates collection quality once before commit; the
  bundle writer validates persisted evidence and does not recalculate it.

## Validation performed

The following checks are required at this head:

```text
python -m compileall -q src       PASS
python -m pytest -q               517 passed
CI-safe allowlist                 517 passed
git diff --check                  PASS
```

The local full regression is `517 passed`; the CI-safe allowlist is rerun as a
separate exact command before release. Exact-head GitHub CI for this unpushed
working tree is intentionally not claimed here.

Local implementation commit (pre-hotfix history):

```text
80da3981f2b7635f2ea4cd09265a45d558a78e75
```

Current final merge-blocker hotfix commit:

```text
896e1eb1e2d0d74cbe0d609fd36c8e8a803ffcf8
```

Follow-up retry-binding fix:

```text
610c637
```

The test suite includes regression coverage for the historical `source_hash`
ordering bug, shared promotion semantics, routing counts/backlog behavior,
reviewer/applier separation (including same-actor rejection and zero
mutation), fixture/formal repair separation, CURRENT scope, QA-aware calendar
baselines, retry-stable metric hashes, persisted state/hash tamper rejection,
required-metric fail-closed behavior, and pre-commit collection orchestration.

## Explicit non-goals

No production extraction, real PRIMARY write, dictionary Apply, AI/model call,
profile change, lifecycle rule change, or `main` modification is part of this
hotfix. Merge/push remain separate human-authorized release actions.
