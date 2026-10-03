# Repository Branch Inventory — 2026-10-03

This inventory was generated from `origin/main` after `git fetch --all --prune`.
The comparison is `origin/main...<branch>`; `behind_main` is the number of
commits present only on main, `ahead_main` is the number present only on the
branch, and `unique_patch_count` is the count reported by
`git cherry origin/main <branch>`. No remote branch was deleted.

The integration row records the comparison at the prior audit snapshot
`3e9830e`; the final follow-up commit only updates these audit documents.

## Current main

`origin/main` = `788e290ece1ec4bb4a2ba110b0ab2ffe1f451864`.

## Branch inventory

| branch | head_sha | merge_base | behind_main | ahead_main | unique_patch_count | category | recommended_action | safe_to_delete_after_consolidation |
|---|---|---|---:|---:|---:|---|---|---|
| main | 788e290 | 788e290 | 0 | 0 | 0 | MAIN | KEEP | no |
| feat/action-data-platform-v2 | 59adcb1 | 59adcb1 | 173 | 0 | 0 | SUPERSEDED | DELETE_AFTER_VERIFICATION | yes |
| feat/chinese-localization-intelligence-v1 | 6c65b73 | 6c65b73 | 113 | 0 | 0 | SUPERSEDED | DELETE_AFTER_VERIFICATION | yes |
| feat/data-quality-integrity-v1 | 88ad0fa | 88ad0fa | 7 | 0 | 0 | SUPERSEDED | DELETE_AFTER_VERIFICATION | yes |
| feat/export-foundation-v1 | dac6f8f | 503a295 | 238 | 22 | 21 | MIGRATION_SOURCE | MIGRATE | no, until export semantics are reviewed |
| feat/localization-knowledge-growth-v1 | aba2838 | aba2838 | 108 | 0 | 0 | SUPERSEDED | DELETE_AFTER_VERIFICATION | yes |
| feat/master-dictionary-export-closure-v1 | 2fe180a | 2fe180a | 66 | 0 | 0 | SUPERSEDED | DELETE_AFTER_VERIFICATION | yes |
| feat/production-operations-v1 | 14bb7ed | 14bb7ed | 198 | 0 | 0 | SUPERSEDED | DELETE_AFTER_VERIFICATION | yes |
| feat/scrapling-detail-shadow-20260924 | 782e4ed | 503a295 | 238 | 27 | 26 | EXPERIMENT | REBUILD | no, until v2 is verified |
| feat/sqlite-data-foundation-v1 | 6e7c594 | 8acfebd | 270 | 6 | 6 | MIGRATION_SOURCE | MIGRATE | no, until migration/mirror safety is reviewed |
| feat/translation-registry-qwen-mt-v1 | ffc6ba0 | 88ad0fa | 7 | 23 | 23 | SUPERSEDED_AFTER_CONSOLIDATION | DO NOT MERGE AGAIN; retain for recovery/audit | no |
| experiment/scrapling-detail-shadow-v2 | 1a68003 | 788e290 | 0 | 2 | 2 | EXPERIMENT | PR | no |
| fix/data-quality-config-fail-closed | 88ad0fa | 88ad0fa | 7 | 0 | 0 | SUPERSEDED | DELETE_AFTER_VERIFICATION | yes |
| fix/legacy-artifact-stage6-provenance | add8734 | 788e290 | 0 | 3 | 3 | MIGRATION_SOURCE | PR | no |
| fix/naming-history-export-20260917 | 0b3795b | 503a295 | 238 | 50 | 49 | MIGRATION_SOURCE | MIGRATE | no, until semantic migration is complete |
| fix/reconcile-uncommitted-master-repairs | 8163053 | 8163053 | 61 | 0 | 0 | SUPERSEDED | DELETE_AFTER_VERIFICATION | yes |
| hotfix/post-merge-production-safety | 58e6ddc | 58e6ddc | 162 | 0 | 0 | SUPERSEDED | DELETE_AFTER_VERIFICATION | yes |
| integrate/localization-hardening-20260922 | e0477ff | e0477ff | 1 | 0 | 0 | SUPERSEDED | DELETE_AFTER_VERIFICATION | yes |
| integrate/repository-consolidation-v1 | 3e9830e | 788e290 | 0 | 28 | 27 | ACTIVE_FEATURE | PR | no |

`origin/main` already contains the complete history of the branches marked
`SUPERSEDED`; their branch heads are ancestors of main and `git cherry` found
no patch-equivalent commits unique to those branches.

The branches with real remaining patches are export foundation (21), Scrapling
shadow (26), SQLite foundation (6), Translation V1 (23), Stage6 provenance (2),
and naming-history (49). These are migration sources or active work and must
not be deleted in this consolidation.

## Recovery tags created

All tags were absent locally and remotely before creation, and each was created
only after resolving its target SHA:

| tag | target |
|---|---|
| `pre-consolidation-main-20261003` | `788e290ece1ec4bb4a2ba110b0ab2ffe1f451864` |
| `archive-naming-history-20261003` | `0b3795b7565c9432ee0419e2dfb8937fad56bdb0` |
| `archive-scrapling-shadow-20261003` | `782e4ed277bd28a6cd7eb1caff2f91479f85fefa` |
| `archive-export-foundation-20261003` | `dac6f8f394e3c29eb197fe7ebedfd029464f4517` |
| `archive-sqlite-foundation-20261003` | `6e7c5940b90d2eec8ef6942c93c7af772af40b31` |

All five tags were also pushed to `origin` and can be used as remote recovery
points. No existing tag was moved.
