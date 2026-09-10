# Data Quality & Integrity V1 — Requirement Self-Audit

This checklist maps the two V1 specifications to current implementation
evidence. It is an audit artifact, not a production approval.

| Requirement | Evidence | Status |
|---|---|---|
| Isolated feature worktree; production worktree untouched | `feat/data-quality-integrity-v1`, clean worktree; no production command was run | PASS |
| No real PRIMARY write, production apply, daily-run, AI/Qwen, or auto-approval | Test fixtures/tmp SQLite only; read-only production audit; branch docs | PASS |
| Shared issue contract and deterministic issue IDs | `src/action_tracker/data_quality/contracts.py`; `tests/test_data_quality_integrity.py` | PASS |
| Additive quality schema and indexes | `data_quality/schema.py`; four requested tables; migration tests | PASS |
| H01–H08 historical issue rules | `historical/audit.py`; dirty-fixture audit test | PASS |
| Historical audit is read-only unless explicit persistence is requested | SQLite `mode=ro` path in `historical/audit.py` | PASS |
| Idempotent issue and candidate persistence | Repository upserts and idempotency tests | PASS |
| Review, approval, base-commit, source-hash, rollback and verification gates | `historical/repair.py`; repair workflow tests | PASS |
| PRIMARY repair apply is forbidden during V1 development | `_assert_not_primary()` and fixture-only adapter test | PASS |
| Deterministic read-only Master Quality Gate | `data_quality/master_gate.py`; clean/dirty fixture tests | PASS |
| Required Master blockers and visible warning categories | `MasterQualityResult`, `format_master_quality()` | PASS |
| Research Release requires Master Quality Gate | `exporting/service.py`, `localization/release_gate.py`, prerequisite test | PASS |
| Per-run collection metrics with explicit UNAVAILABLE values | `collection/metrics.py`; metric persistence tests | PASS |
| Per-category counts and category completion evidence | `category_1_count` scoped metrics; daily run report integration | PASS |
| Healthy previous/7-day/30-day baselines; unhealthy runs excluded | `collection/baseline.py`; baseline tests | PASS |
| Persisted baseline and delta fields | `collection/gates.py`; persistence test | PASS |
| COLLECTION_OK/WARN/DEGRADED/BLOCKED states | `collection/gates.py`; collection tests | PASS |
| BLOCKED/degraded commit policy and bounded override | `ProductionWriter`, `validate_collection_override()` and gate tests | PASS |
| Schema drift types and evidence | `collection/drift.py`; coverage/drift tests; evidence includes previous, 7d, 30d, delta, samples and optional source hash | PASS |
| PRIMARY commit evaluates collection integrity before product transaction | `database/integration.py`; PRIMARY blocking test | PASS |
| Existing Presence/Lifecycle core remains unchanged | No edits to monitor/lifecycle decision modules; full regression | PASS |
| Required CLI surface | `cli.py`; `--help` checks for audit/repair/master/collection commands | PASS |
| New test module is CI-safe allowlisted | `tests/ci_safe_tests.txt`; allowlist test and exact local allowlist run | PASS |
| Full local regression | `python -m pytest -q` → 504 passed | PASS |
| Exact local CI-safe suite | `tests/ci_safe_tests.txt` → 504 passed | PASS |
| Real PRIMARY read-only audit | Prior audit: 5,536 current records, release-ready, file hash/mtime unchanged, writes 0 | PASS (read-only) |
| Ubuntu/Windows exact-head GitHub Actions | Actual branch push has not been performed; non-mutating push dry-run succeeded, but no exact-head run exists | UNVERIFIED |
| Merge/push/production activation | Explicitly not performed by design | PENDING AUTHORIZATION |

## Current validation heads

- Feature code/tests head: `e95fcfec609ecd56a8812176915567a9c9e01f2b`
- Code head recorded in the acceptance report: `e016b20cd87fda0aa501020bd232be3a58dc1a86`
- Local `main` baseline: `e8c1a16c31743ecfacec05a953c22d5c65d9fca6`
- Cached `origin/main`: `2fe180ab6d99d1ed8982fe6fb7d03a432e5d4400`

Commits after the recorded code head only update acceptance metadata. No
production data or runtime state is part of this worktree.

## Decision

Implementation and local self-audit are complete. Final release acceptance is
not yet claimable until the feature branch is explicitly pushed and the
Ubuntu/Windows exact-head CI results are available.
