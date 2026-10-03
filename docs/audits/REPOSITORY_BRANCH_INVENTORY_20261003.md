# Repository Branch Inventory — 2026-10-03

基线：`origin/main@b47e4c56a0274fdddceff347e2571d77c1476014`。
`behind/ahead` 是相对 main 的 `git rev-list --left-right --count main...branch`；`unique` 是 `git rev-list --count main..branch`。本表只做审计，不删除分支。

| branch | head | merge-base | behind/ahead | unique patch | category | recommended_action | safe_to_delete | recovery_tag |
|---|---|---|---:|---:|---|---|---|---|
| main | b47e4c5 | b47e4c5 | 0/0 | 0 | CURRENT_MAIN | retain | NO | pre-consolidation-main-20261003 |
| feat/translation-registry-qwen-mt-v1 | ffc6ba0 | ffc6ba0 | 18/0 | 0 | SUPERSEDED_AFTER_CONSOLIDATION | DELETE_AFTER_OWNER_CONFIRMATION | OWNER_CONFIRMATION_REQUIRED | — |
| integrate/repository-consolidation-v1 | abe0237 | abe0237 | 1/0 | 0 | SUPERSEDED_AFTER_CONSOLIDATION | DELETE_AFTER_OWNER_CONFIRMATION | OWNER_CONFIRMATION_REQUIRED | — |
| feat/action-data-platform-v2 | 59adcb1 | 59adcb1 | 207/0 | 0 | SUPERSEDED_AFTER_CONSOLIDATION | DELETE_AFTER_OWNER_CONFIRMATION | OWNER_CONFIRMATION_REQUIRED | — |
| feat/chinese-localization-intelligence-v1 | 6c65b73 | 6c65b73 | 147/0 | 0 | SUPERSEDED_AFTER_CONSOLIDATION | DELETE_AFTER_OWNER_CONFIRMATION | OWNER_CONFIRMATION_REQUIRED | — |
| feat/data-quality-integrity-v1 | 88ad0fa | 88ad0fa | 41/0 | 0 | SUPERSEDED_AFTER_CONSOLIDATION | DELETE_AFTER_OWNER_CONFIRMATION | OWNER_CONFIRMATION_REQUIRED | — |
| feat/localization-knowledge-growth-v1 | aba2838 | aba2838 | 142/0 | 0 | SUPERSEDED_AFTER_CONSOLIDATION | DELETE_AFTER_OWNER_CONFIRMATION | OWNER_CONFIRMATION_REQUIRED | — |
| feat/master-dictionary-export-closure-v1 | 2fe180a | 2fe180a | 100/0 | 0 | SUPERSEDED_AFTER_CONSOLIDATION | DELETE_AFTER_OWNER_CONFIRMATION | OWNER_CONFIRMATION_REQUIRED | — |
| feat/production-operations-v1 | 14bb7ed | 14bb7ed | 232/0 | 0 | SUPERSEDED_AFTER_CONSOLIDATION | DELETE_AFTER_OWNER_CONFIRMATION | OWNER_CONFIRMATION_REQUIRED | — |
| fix/data-quality-config-fail-closed | 88ad0fa | 88ad0fa | 41/0 | 0 | SUPERSEDED_AFTER_CONSOLIDATION | DELETE_AFTER_OWNER_CONFIRMATION | OWNER_CONFIRMATION_REQUIRED | — |
| fix/reconcile-uncommitted-master-repairs | 8163053 | 8163053 | 95/0 | 0 | SUPERSEDED_AFTER_CONSOLIDATION | DELETE_AFTER_OWNER_CONFIRMATION | OWNER_CONFIRMATION_REQUIRED | — |
| hotfix/post-merge-production-safety | 58e6ddc | 58e6ddc | 196/0 | 0 | SUPERSEDED_AFTER_CONSOLIDATION | DELETE_AFTER_OWNER_CONFIRMATION | OWNER_CONFIRMATION_REQUIRED | — |
| integrate/localization-hardening-20260922 | e0477ff | e0477ff | 35/0 | 0 | SUPERSEDED_AFTER_CONSOLIDATION | DELETE_AFTER_OWNER_CONFIRMATION | OWNER_CONFIRMATION_REQUIRED | — |
| feat/export-foundation-v1 | dac6f8f | 503a295 | 272/22 | 22 | MIGRATION_SOURCE | retain pending audit; no auto-delete | NO | archive-export-foundation-20261003 |
| feat/scrapling-detail-shadow-20260924 | 782e4ed | 503a295 | 272/27 | 27 | EXPERIMENT_SOURCE | supersede with scrapling-v3 after tests | NO | archive-scrapling-shadow-20261003 |
| experiment/scrapling-detail-shadow-v2 | 1a68003 | 788e290 | 34/2 | 2 | SUPERSEDED_EXPERIMENT | retain until v3 owner review | NO | archive-scrapling-shadow-20261003 |
| feat/sqlite-data-foundation-v1 | 6e7c594 | 8acfebd | 304/6 | 6 | MIGRATION_SOURCE | retain; schema/PRIMARY changes require separate project | NO | archive-sqlite-foundation-20261003 |
| fix/legacy-artifact-stage6-provenance | add8734 | 788e290 | 34/3 | 3 | SUPERSEDED_STAGE6 | use only allowlisted commits on stage6-v2 | NO | — |
| fix/naming-history-export-20260917 | 0b3795b | 503a295 | 272/50 | 50 | MIGRATION_ONLY_SOURCE | inventory only; do not merge branch | NO | archive-naming-history-20261003 |

## Recovery tag verification

The following tags were present locally at preflight and were not moved or deleted:

- `pre-consolidation-main-20261003` → `f4e7678`
- `archive-naming-history-20261003` → `2f7f1dd`
- `archive-scrapling-shadow-20261003` → `84fb9a4`
- `archive-export-foundation-20261003` → `6002c58`
- `archive-sqlite-foundation-20261003` → `9e3fca9`

Network fetch was unavailable during this run, so remote tag parity must be rechecked after connectivity returns.

## Decision rule

`safe_to_delete` is never set to `YES` by this audit. A branch with `ahead=0` and `unique=0` is only a deletion candidate after explicit owner confirmation and a final remote fetch. Branches with unique patches remain retained for migration or experiment review.
