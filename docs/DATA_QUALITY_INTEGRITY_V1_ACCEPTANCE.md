# Data Quality & Integrity V1 — Acceptance Draft

> Status note: the numbers and CI references below are the pre-hotfix
> acceptance snapshot. For the current closure blockers and local validation,
> use `DATA_QUALITY_INTEGRITY_V1_FINAL_CLOSURE_HOTFIX.md`. This branch remains
> unpushed and unmerged until separate human authorization.

This draft is generated on the isolated feature worktree. It is not a merge or
production approval.

## Current final merge-blocker hotfix verification

The current working tree contains the final three blocker fixes: an explicit
human-only repair applier with reviewer/applier separation, QA-aware baseline
eligibility, and deterministic persisted collection-evidence state/hash
binding inside the writer transaction. Local validation is complete:

```text
full regression: 517 passed
CI-safe allowlist: 517 passed
compileall: PASS
git diff --check: PASS
exact-head GitHub CI for this unpushed hotfix: NOT RUN
```

The feature branch remains unmerged. A push and exact-head GitHub CI run are
separate release actions requiring explicit authorization.

```text
DATA QUALITY & INTEGRITY V1

branch: feat/data-quality-integrity-v1
base_main: e8c1a16 (local main at worktree creation)
head: 55f4440cf0b4c0a3a1863a3a5c74c230db6f0295

MODULE A — Historical Master Repair
issues_supported: 8 initial issue types
audit: read-only SQLite inspection + optional issue persistence
candidate_build: deterministic/idempotent repair batches and CSV/JSON preview
review: explicit APPROVED/REJECTED actor and timestamp
apply_adapter: fixture-only, dry-run by default; PRIMARY rejected
verification: applied candidate and resolved issue checks

MODULE B — Master Quality Gate
blocker_rules: SKU, price, contamination, history, provenance and source hash
warning_rules: category, description/detail, image and legacy evidence gaps
release_gate_integration: Research Release prerequisite for SQLite_CURRENT
clean_fixture: RELEASE_READY
dirty_fixture: RELEASE_BLOCKED

MODULE C — Collection Integrity
metrics: per-run persisted metrics with explicit UNAVAILABLE values
7d_baseline: healthy median
30d_baseline: healthy median
collection_gate: OK / WARN / DEGRADED / BLOCKED
schema_drift: coverage, price semantics, UI and HTML drift evidence
override: bounded one-shot actor/reason/run/hash/expiration contract

DATABASE
new_tables: data_quality_issues, collection_quality_metrics, repair_batches, repair_candidates
migrations: additive only
real_primary_written: NO

TESTS
targeted: 109 passed (quality + database + lifecycle + release related tests)
full: 505 passed
failed: 0

CI COVERAGE
test_modules: tests/test_data_quality_integrity.py
allowlisted: YES
missing: 0
local_ci_safe: 505 passed

EXACT HEAD CI
head: 55f4440cf0b4c0a3a1863a3a5c74c230db6f0295
run_id: 34430477401
run_url: https://github.com/zjwjoey/ActionSKUTracker/actions/runs/34430477401
Ubuntu: PASS (24s)
Windows: PASS (1m45s)
latest_documentation_head: 31925cfe7446a2f9e4aee66bc26e19954ee63476
latest_documentation_ci_run: 34430661088
latest_documentation_ci: Ubuntu PASS (25s), Windows PASS (3m23s)

REAL PRIMARY READ-ONLY AUDIT
database: F:\\ActionSKUTracker\\runtime\\db\\action_tracker.db
master_quality: PASS / RELEASE_READY
current_records_checked: 5536
master_warnings: CATEGORY_MISSING=25, DESCRIPTION_OR_DETAILS_MISSING=3
historical_audit: 26 CATEGORY_MISSING (warnings only)
file_hash_and_mtime_unchanged: YES
writes: 0

FINAL
DATA_QUALITY_INTEGRITY_V1
READY_FOR_REVIEW
```

Before merge, run the CI workflow against this exact feature HEAD and attach
the Ubuntu and Windows results to this document. Do not merge, push, apply a
repair, or run a real daily extraction as part of this acceptance draft.
