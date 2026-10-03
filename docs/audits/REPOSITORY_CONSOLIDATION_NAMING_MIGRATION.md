# Naming-history Semantic Migration Audit — 2026-10-03

Source branch: `origin/fix/naming-history-export-20260917` at
`0b3795b7565c9432ee0419e2dfb8937fad56bdb0`.

This branch is a long fork: 50 commits ahead of main, 238 commits behind, and
49 commits remain unique after `git cherry`. It was **not merged** into main or
the Translation integration branch. The archive tag is
`archive-naming-history-20261003`.

## Migration classification

| item | representative paths | disposition | action |
|---|---|---|---|
| A. Detail semantic governance | `src/action_tracker/translation/detail_review_contract.py`, `detail_terminology.py`, `detail_rule_repair.py`, `tests/test_detail_*.py` | MIGRATE_CODE / MIGRATE_TEST | Port only the contracts and field-scoped checks that are absent from the current localization/QA stack; rerun source-bound and duplicate-key tests. |
| B. Stage5 governance | `src/action_tracker/stage5/**`, `tests/test_stage5_*.py`, stage5 policy docs | MIGRATE_CODE / MIGRATE_CONTRACT | Reconcile against the current Stage5 ownership and guard contracts. Do not copy whole files over current production modules. |
| C. Stage6 governance | `src/action_tracker/stage6/preview.py`, Stage6 scripts/docs/tests | MIGRATE_CODE / MIGRATE_TEST | The two-commit `fix/legacy-artifact-stage6-provenance` line is the cleaner migration source. Review it independently as a PR; do not import naming-history's whole Stage6 tree. |
| D. Localization regression | `src/action_tracker/localization/{gold_gate,repair_service,shadow_audit}.py`, `tests/test_localization_*.py` | MIGRATE_TEST / MIGRATE_CODE | Compare each regression against Translation V1 canonical QA, release gate, and source-empty semantics. Keep only tests that expose a current gap. |
| E. Terminology rules | `src/action_tracker/translation/{approved_terms,term_resolver,title_policy}.py`, detail terminology rules | MIGRATE_CONTRACT / MIGRATE_CODE | Merge rule meaning into the current terminology repository and field-scoped QA. Preserve numeric, unit, model, and brand token protection. |
| F. Dictionary data | `data/dictionary/*.csv`, `baseline_manifest.json`, localization manifests | MIGRATE_DATA_AFTER_REVIEW | Never overwrite the current baseline. Recompute hashes, verify source_hash and provenance, check owner review, produce a row-level diff, and pass the existing release gate before any data migration. |
| G. QA rules | `src/action_tracker/qa/**`, export/release gates, guard-status tests | MIGRATE_CODE / MIGRATE_TEST | Port narrowly where a rule is not already present. Preserve SQLite PRIMARY, production transaction, immutable patch, and field-level approval gates. |
| H. Historical one-shot scripts | `scripts/*202609*.py`, training/recovery/export scripts | ARCHIVE_ONLY | Keep in the tagged branch for reproducibility. Do not add historical runtime scripts to the production command path. |
| I. Docs / audit evidence | `docs/STAGE*`, Qwen reports, remediation reports, audit indexes | ARCHIVE_ONLY / MIGRATE_CONTRACT | Retain as evidence. Copy only current contracts or a dated reconciliation summary after fact review. |

## Explicit non-migrations

The following must not be replaced by versions from the naming branch:

- `AGENTS.md`, `README.md`, `docs/CURRENT_STATE.md`, `config/settings.yaml`;
- `data/dictionary/product_dictionary.csv`, `manual_overrides.csv`,
  `model_translation_overrides.csv`, and `baseline_manifest.json`;
- `src/action_tracker/database/production.py` and `schema.py`.

The naming branch contains broad dictionary and database changes. Those diffs
are evidence for review, not a safe merge unit. Current SQLite PRIMARY and the
current translation integration remain the source of truth.

## Decision

`fix/naming-history-export-20260917` is a migration source and archive-only
history, not a merge candidate. The next safe work is a sequence of small PRs
for the A–G items above, each with a focused diff and its own release-gate
evidence.
