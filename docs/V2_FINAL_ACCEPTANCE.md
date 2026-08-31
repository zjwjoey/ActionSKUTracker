# Architecture V2 Acceptance Record

This document is updated after the feature branch audit. Acceptance requires:
SQLite integrity/FK and lifecycle parity PASS; deterministic extraction,
selection semantics and artifact SKU exactness; full regression and exact-head
CI PASS; localhost-only Workspace; and no HIGH/MEDIUM findings.

Scheduler observation is operational evidence, not a reason to alter product
facts. The final report must state real database counts, query samples and
artifact hashes rather than relying on historical SHA values.

## Current evidence (2026-08-31)

- SQLite PRIMARY: 8,680 products; 5,379 CURRENT, 650 OFFLINE, 17 MISSING,
  2,634 HISTORICAL; lifecycle rows 6,046; projection mismatch 0; integrity and
  foreign-key checks PASS. The 2026-08-31 production run is the current DB
  head; the post-run projection reconciliation is recorded with a SQLite
  Backup-API backup.
- Real Extraction samples: CURRENT 5,379; CURRENT price <= 2: 2,212;
  recent new: 597; price decreased: 945; OFFLINE: 36; image AVAILABLE:
  5,379; localization incomplete: 0; keyword `microfibra`: 16; category
  `Hogar`: 463. The price query used `idx_products_status_price`.
- Real Selection `sel_900a37b4339a`: 50 fixed members from CURRENT price <=
  0.5. CSV, ES XLSX, ZH XLSX, ZH-with-images XLSX and a 50-image ZIP were
  generated with exact membership and artifact hashes recorded in SQLite.
- Local regression: 325 passed. Exact-head CI for branch HEAD `2e98b40`:
  `33352065427 PASS`. Main is unchanged and no merge is automatic.
