# P0–P2 Data Foundation Freeze — 2026-08-30

## Baseline

- Parent/main: `a3ce3de` (`merge: release p2 image foundation`)
- P0/P1 merge: `c015bfe`; P2 merge: `a3ce3de`
- Main CI: GitHub run `33311589488`, PASS
- SQLite production database: `F:\ActionSKUTracker\runtime\db\action_tracker.db`
- Role: `PRIMARY`; schema family `ACTION_SQLITE_DATA`, schema version `2.0.0`
- Real CURRENT count: **5,396** (read from SQLite, not hardcoded)

## Release state

| Foundation | State | Boundary |
|---|---|---|
| P0 Export | RELEASED | Generated ES/ZH exports and manifests; no website access during export |
| P1 SQLite | PRIMARY_ACCEPTED | SQLite is product/lifecycle structured truth; Excel is output |
| P2 Images | RELEASED | `image_assets` metadata + binary/derivative pipeline; images remain output assets |

## P2 evidence

The final reacceptance recorded 5,396/5,396 eligible images and zero SKU/business-fact mismatches for ES, ZH and Template1 with-images exports. Row-height and embedded-image checks passed. The latest main CI remained green after the merge.

## Frozen invariants

Knowledge and export work may not mutate Spanish facts, SKU identity, prices, events, lifecycle, or image metadata. Presence is frozen before detail enrichment. AI candidates and review artifacts are not formal localization. P0–P2 changes after this date require a bug, integrity, or security justification and their own regression evidence.
