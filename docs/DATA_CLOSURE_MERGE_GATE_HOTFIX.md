# Data Closure Merge Gate Hotfix

This branch closes the merge-gate risks around the localization, dictionary,
and export boundary. It does not change collection, lifecycle, price capture,
Cloudflare handling, or the production database.

## Contract changes

- Production localization writes require an approved immutable patch, the
  current committed base, the current Spanish source hash, and the patch's
  old value. The field update, provenance, commit batch, run evidence, export
  sync row, and `PATCH_APPLIED` event are one SQLite transaction.
- Provenance is canonical in `localization_fields`. Approval, freshness, and
  source hash are preserved per field; aggregate localization status is not
  fanned out to unrelated fields.
- The active `product_fact_versions` table remains authoritative when it
  already exists. `source_fact_versions` is retained only for legacy/fresh
  schemas that have no active product fact store.
- Research-release audits receive the final export projection. The explicit
  display rule for `原价` treats a missing, equal, or lower original price as
  an empty export value; a true discount still requires `原价 > 折后价`.
- Candidate builders require explicit source paths and open the source DB
  read-only before copying it with SQLite Backup API.
- Category approvals require official Action evidence, SKU/page identity, and
  a current source hash.
- Export pair publication records whether old files existed and only rolls
  back files that were actually replaced.

## Local evidence

The isolated candidate was built from a read-only copy of the current PRIMARY
database and the reviewed Chinese export. No production write occurred.

```
records_checked: 5547
SKU_SET_MISMATCH: 0
FACT_MISMATCH: 0
UNDECLARED_DISPLAY_MISMATCH: 0
UNAPPROVED_ZH: 0
STALE_ZH: 0
SPANISH_RESIDUAL: 0
SOURCE_HASH_MISMATCH: 0
LOCALIZATION_PROJECTION_MISMATCH: 0
SQLite integrity_check: PASS
foreign_key_check: 0
fact_version_authority: product_fact_versions
```

The candidate patch lifecycle also passed on an active-PRIMARY-shaped schema:
`PATCH_CREATED → PATCH_APPROVED → PATCH_APPLIED → PATCH_REVOKED`. A one-field
description apply changed no other field's value, review status, freshness, or
source hash.

## Verification

The full local suite passes (`449 passed`). The CI-safe allowlist includes the
field provenance, immutable patch, research release, category evidence,
partial apply, knowledge production, and export atomicity modules. Exact-head
run `34250080673` for commit
`177f2736ab26879a04b83181d766da0bdef8aecb` passed on both Ubuntu and Windows;
each job reported `449 passed`. The Node.js 20 deprecation notice is an
upstream action warning and did not fail the run.
