# P3–P6 Knowledge Production V1 Final Acceptance — 2026-08-30

## Branch and baseline

- Branch: `feat/knowledge-production-v1`
- Parent main: `a3ce3de`
- Branch HEAD: `7302442`
- GitHub CI: run `33312031121`, PASS (CI-safe tests)
- P0/P1/P2: released/accepted on main; see `P0_P2_DATA_FOUNDATION_FREEZE_20260830.md`.
- Production switches remain disabled: dictionary apply, Knowledge apply, scoped dictionary, AI translation and auto approval.

## Real SQLite PRIMARY audit

Read-only audit source: `F:\ActionSKUTracker\runtime\db\action_tracker.db`.

- Role: PRIMARY; integrity check `ok`; foreign-key check empty.
- CURRENT: 5,396; formal zh localization rows: 5,396.
- Field coverage: name 5,396; cat1 5,396; cat2 5,379; spec 5,396; description 5,378; details 5,379.
- Freshness: 5,396 `CURRENT` rows as stored; no stale mismatch was detected (the product table has no populated source hash, so future runs must populate/compare the six-field hash).
- Queue: 0 persisted pending rows; candidates: 0 persisted rows.
- Live provider: not configured/validated; this is intentionally not represented as a successful live pilot.

Full JSON evidence: `F:\ActionSKUTracker\runtime\temp\knowledge_audit_20260830.json`.

## Acceptance by stage

### P3 Dictionary Production — RELEASED (offline/fixture scope)

Six-field source hash, field-level resolver, stale preview rejection, explicit PRIMARY/apply gate, field-level localization writes, provenance and export isolation are covered. No Master or Spanish fact writer exists in this path.

### P4 Scoped Dictionary — RELEASED (shadow/fixture scope)

Current Spanish category matching is deterministic in the order PRODUCT > CAT2+FIELD > CAT2 > CAT1+FIELD > CAT1 > FIELD > GLOBAL. Same-specificity conflicts fail closed. Only human-approved rules match and blast-radius evidence is available. Switch remains off.

### P5 Incremental AI Translation — RELEASED (offline code scope)

Stable queue IDs, deduplication, source-change invalidation, provider injection, bounded retry, numeric-fact validation and candidate-only persistence are covered. AI candidates cannot enter formal export or localization. Live provider validation: NO.

### P6 Auto Approval — SHADOW_RELEASED

Only low-risk category/spec fields can be considered; name/description/details require review. Validator, source freshness, conflict and human-priority gates are retained. Confidence alone is insufficient. Kill switch remains off and no AI dictionary promotion exists.

## Safety and parity

Knowledge writes are limited to the six Chinese localization fields. Spanish facts, SKU identity, lifecycle, price/events, image metadata and Master files are outside the write set. P0–P2 regression remains green on the parent main. Formal ES/ZH/with-images parity is unchanged from the P2 acceptance.

## Findings

- HIGH: 0
- MEDIUM: 0
- LOW: live provider pilot not run; product source hash population should be monitored before enabling production localization apply.

P3–P6 are ready for user review on this branch. This task does **not** merge the Knowledge branch into main.
