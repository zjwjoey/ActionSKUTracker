# Repository Consolidation V1 — 2026-10-03

## Scope and safety

- Base: `origin/main` at `788e290ece1ec4bb4a2ba110b0ab2ffe1f451864`.
- New integration branch: `integrate/repository-consolidation-v1`.
- Translation V1 was merged into this new branch with one allowlist conflict
  resolved by taking the union of both safe-test lists.
- No main branch was pushed, merged into, reset, rebased, force-pushed, or
  deleted. No production runtime data or dated source Excel was changed.
- Existing dirty worktrees were left untouched.

## Translation System V1

The integration contains the registry, providers, memory, terminology,
canonical QA, worker, retranslation, product-family, queue, and release-gate
components from `feat/translation-registry-qwen-mt-v1`. The merge retained
main's SQLite PRIMARY, data-quality, production transaction, source provenance,
field-scoped review, and release-gate paths. The integrated branch passed both
the full local suite and the CI-safe allowlist: **682 passed** in each run.

This is suitable for an integration PR. It is not a production collection
result: `LOCAL_ONLY_NOT_RUN` and `REAL_COLLECTION_NOT_RUN` remain true for this
repository consolidation.

## Stage6 provenance

`fix/legacy-artifact-stage6-provenance` is directly based on current main and
adds two provenance commits plus one date-stability test-only commit. Its diff is limited to the legacy adapter,
fail-closed preview checks, a read-only preview script, requirement matrix, and
tests. The focused Stage6 suite passed **24 tests** and the branch CI-safe allowlist
passed **562 tests**. It is `READY_FOR_PR`.

## SQLite foundation

SQLite PRIMARY itself is already current architecture, but the SQLite foundation
branch is **not completely superseded**. Main does not contain the branch's
`database/migration.py`, `database/mirror.py`, `database/validation.py`, or
`tests/test_sqlite_mirror.py`. Those six unique patches are migration sources;
they require a selective safety review and must not be merged as a whole.

## Scrapling v2

The old Scrapling branch remains a shadow experiment. A separate branch
`experiment/scrapling-detail-shadow-v2` is rebuilt from current main using only
the following files:

```text
requirements-experiments/scrapling-detail-shadow.txt
scripts/experiments/run_scrapling_detail_shadow.py
src/action_tracker/experiments/__init__.py
src/action_tracker/experiments/scrapling_detail/**
tests/experiments/test_scrapling_detail_shadow.py
tests/fixtures/scrapling_detail/**
```

Scrapling remains outside production requirements and outside formal daily
collection. The v2 branch is shadow-only; its focused experiment suite passed
20 tests (with optional dependency installed), and its remote CI run passed.

## Documentation facts requiring reconciliation

1. `AGENTS.md` still says “SQLite code remains frozen” while README,
   CURRENT_STATE, ARCHITECTURE, DATA_MODEL, and settings identify SQLite
   PRIMARY as the production read source. The intended interpretation should be
   recorded as: schema/migration changes require a separate reviewed project;
   SQLite PRIMARY itself is not frozen out of production.
2. Older README/CURRENT_STATE sections retain historical Excel/CSV wording and
   dated counts. Current architecture text already says Excel/CSV are
   compatibility projections; historical counts must remain dated evidence, not
   permanent rules.
3. `文本文档.txt` is absent from the current main checkout. If found in an
   external working copy, propose moving it to `docs/archive/legacy-prompts/`
   after content review; do not delete it as part of consolidation.

## CI governance audit

The current consolidation branch has no `test_*.py` file outside
`tests/ci_safe_tests.txt`; the allowlist run and full pytest both cover 682
tests. This is a verified snapshot, not a scalable policy. The follow-up design
is to add pytest markers (`ci_safe`, `local_only`, `browser`, `network`,
`production`) and make the default safe run marker-driven. Do not combine that
large CI refactor with this feature integration. `ruff`, coverage, and a
project-level `pyproject.toml` are follow-up options, not consolidation
requirements.

## Consolidation status

`READY_FOR_MERGE_PHASE`: the integration branch is tested and ready for an
owner-reviewed PR. Old branches remain available; no deletion was performed.

## Remote artifacts

| artifact | remote ref / SHA |
|---|---|
| integration branch | `integrate/repository-consolidation-v1` / `03c33914148ec3a04205363099a34bdd21753b34` |
| Scrapling v2 branch | `experiment/scrapling-detail-shadow-v2` / `1a68003198c66d4bc04cfc9b332a45f7a1e2f64d` |
| Stage6 provenance branch | `fix/legacy-artifact-stage6-provenance` / `add8734aaa72c8794a72de97bf893312bda9e5e5` |
