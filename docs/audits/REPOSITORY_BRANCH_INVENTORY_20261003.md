# Repository Branch Inventory — 2026-10-04

基线：`origin/main@b47e4c56a0274fdddceff347e2571d77c1476014`。

## 统计口径

对每个远端分支执行 `git rev-list --left-right --count origin/main...origin/<branch>` 和 `git cherry origin/main origin/<branch>`。

- `behind_main` / `ahead_main`：`rev-list --left-right` 的左值 / 右值。
- `unique_patch_count`：`git cherry` 输出中 `+` 行数量，表示没有 patch-equivalent 的提交。
- `patch_equivalent_count`：`git cherry` 输出中 `-` 行数量，表示已在 main 中找到 patch-equivalent 的提交。
- 本表没有使用 `git rev-list --count main..branch` 冒充 unique patch。

## 远端分支

| branch | head_sha | merge_base | behind_main | ahead_main | unique_patch_count | patch_equivalent_count | category | recommended_action | safe_to_delete | recovery_tag |
|---|---|---|---:|---:|---:|---:|---|---|---|---|
| main | b47e4c56a0274fdddceff347e2571d77c1476014 | b47e4c56a0274fdddceff347e2571d77c1476014 | 0 | 0 | 0 | 0 | CURRENT_MAIN | retain | NO | pre-consolidation-main-20261003 |
| chore/repository-consolidation-closure-v1 | 55368cacd85475872c8033d49684c37ee4a13fbc | b47e4c56a0274fdddceff347e2571d77c1476014 | 0 | 6 | 6 | 0 | CURRENT_CLOSURE_CANDIDATE | owner review; do not merge automatically | NO | — |
| fix/stage6-provenance-v2 | 47971fd7f8e1ecbe41b8dcc25fcb9cc8a8dac5f4 | b47e4c56a0274fdddceff347e2571d77c1476014 | 0 | 2 | 2 | 0 | CURRENT_STAGE6_CANDIDATE | owner review; keep isolated | NO | — |
| experiment/scrapling-detail-shadow-v3 | c1a3ffce069d0a02cb5780aaa6fadf04167c320b | b47e4c56a0274fdddceff347e2571d77c1476014 | 0 | 1 | 1 | 0 | CURRENT_EXPERIMENT | owner review; keep shadow-only | NO | — |
| feat/translation-registry-qwen-mt-v1 | ffc6ba0aed5d47a2f31f9a2aa16c0f3a75e2a279 | ffc6ba0aed5d47a2f31f9a2aa16c0f3a75e2a279 | 18 | 0 | 0 | 0 | SUPERSEDED_AFTER_CONSOLIDATION | DELETE_AFTER_OWNER_CONFIRMATION | OWNER_CONFIRMATION_REQUIRED | — |
| integrate/repository-consolidation-v1 | abe0237dfaebf90ebd19bda3fdb45bf287e99f9e | abe0237dfaebf90ebd19bda3fdb45bf287e99f9e | 1 | 0 | 0 | 0 | SUPERSEDED_AFTER_CONSOLIDATION | DELETE_AFTER_OWNER_CONFIRMATION | OWNER_CONFIRMATION_REQUIRED | — |
| feat/action-data-platform-v2 | 59adcb1c83998147de9ea0bc179f3a943730efac | 59adcb1c83998147de9ea0bc179f3a943730efac | 207 | 0 | 0 | 0 | SUPERSEDED_AFTER_CONSOLIDATION | DELETE_AFTER_OWNER_CONFIRMATION | OWNER_CONFIRMATION_REQUIRED | — |
| feat/chinese-localization-intelligence-v1 | 6c65b73c36d9ef338fd813a1c048fa3e4c8cb3c2 | 6c65b73c36d9ef338fd813a1c048fa3e4c8cb3c2 | 147 | 0 | 0 | 0 | SUPERSEDED_AFTER_CONSOLIDATION | DELETE_AFTER_OWNER_CONFIRMATION | OWNER_CONFIRMATION_REQUIRED | — |
| feat/data-quality-integrity-v1 | 88ad0faff0f12de17dca86e955f5f60c3da955c3 | 88ad0faff0f12de17dca86e955f5f60c3da955c3 | 41 | 0 | 0 | 0 | SUPERSEDED_AFTER_CONSOLIDATION | DELETE_AFTER_OWNER_CONFIRMATION | OWNER_CONFIRMATION_REQUIRED | — |
| feat/localization-knowledge-growth-v1 | aba283860f8a7aaf50fbbb68a62e6cfea3f1f183 | aba283860f8a7aaf50fbbb68a62e6cfea3f1f183 | 142 | 0 | 0 | 0 | SUPERSEDED_AFTER_CONSOLIDATION | DELETE_AFTER_OWNER_CONFIRMATION | OWNER_CONFIRMATION_REQUIRED | — |
| feat/master-dictionary-export-closure-v1 | 2fe180ab6d99d1ed8982fe6fb7d03a432e5d4400 | 2fe180ab6d99d1ed8982fe6fb7d03a432e5d4400 | 100 | 0 | 0 | 0 | SUPERSEDED_AFTER_CONSOLIDATION | DELETE_AFTER_OWNER_CONFIRMATION | OWNER_CONFIRMATION_REQUIRED | — |
| feat/production-operations-v1 | 14bb7ed34e8af574c7ff2db518007a5b60ee4248 | 14bb7ed34e8af574c7ff2db518007a5b60ee4248 | 232 | 0 | 0 | 0 | SUPERSEDED_AFTER_CONSOLIDATION | DELETE_AFTER_OWNER_CONFIRMATION | OWNER_CONFIRMATION_REQUIRED | — |
| fix/data-quality-config-fail-closed | 88ad0faff0f12de17dca86e955f5f60c3da955c3 | 88ad0faff0f12de17dca86e955f5f60c3da955c3 | 41 | 0 | 0 | 0 | SUPERSEDED_AFTER_CONSOLIDATION | DELETE_AFTER_OWNER_CONFIRMATION | OWNER_CONFIRMATION_REQUIRED | — |
| fix/reconcile-uncommitted-master-repairs | 8163053484ff4d10014bdab8cf7e949ab3324794 | 8163053484ff4d10014bdab8cf7e949ab3324794 | 95 | 0 | 0 | 0 | SUPERSEDED_AFTER_CONSOLIDATION | DELETE_AFTER_OWNER_CONFIRMATION | OWNER_CONFIRMATION_REQUIRED | — |
| hotfix/post-merge-production-safety | 58e6ddc43cc92bce6322eacce30ecf956cc2bf44 | 58e6ddc43cc92bce6322eacce30ecf956cc2bf44 | 196 | 0 | 0 | 0 | SUPERSEDED_AFTER_CONSOLIDATION | DELETE_AFTER_OWNER_CONFIRMATION | OWNER_CONFIRMATION_REQUIRED | — |
| integrate/localization-hardening-20260922 | e0477ff854015476bece9a0157c124c387f248f0 | e0477ff854015476bece9a0157c124c387f248f0 | 35 | 0 | 0 | 0 | SUPERSEDED_AFTER_CONSOLIDATION | DELETE_AFTER_OWNER_CONFIRMATION | OWNER_CONFIRMATION_REQUIRED | — |
| feat/export-foundation-v1 | dac6f8f394e3c29eb197fe7ebedfd029464f4517 | 503a2950ce3633ed491653b03c6d52c01859af5f | 272 | 22 | 21 | 1 | MIGRATION_SOURCE | retain pending migration audit | NO | archive-export-foundation-20261003 |
| feat/scrapling-detail-shadow-20260924 | 782e4ed277bd28a6cd7eb1caff2f91479f85fefa | 503a2950ce3633ed491653b03c6d52c01859af5f | 272 | 27 | 26 | 1 | EXPERIMENT_SOURCE | superseded experiment; retain evidence | NO | archive-scrapling-shadow-20261003 |
| experiment/scrapling-detail-shadow-v2 | 1a68003198c66d4bc04cfc9b332a45f7a1e2f64d | 788e290ece1ec4bb4a2ba110b0ab2ffe1f451864 | 34 | 2 | 2 | 0 | SUPERSEDED_EXPERIMENT | retain until v3 owner review | NO | archive-scrapling-shadow-20261003 |
| feat/sqlite-data-foundation-v1 | 6e7c5940b90d2eec8ef6942c93c7af772af40b31 | 8acfebd1a587e94cdc8db19ec93118998c7ca9c7 | 304 | 6 | 6 | 0 | MIGRATION_SOURCE | retain; schema/PRIMARY changes require separate project | NO | archive-sqlite-foundation-20261003 |
| fix/legacy-artifact-stage6-provenance | add8734aaa72c8794a72de97bf893312bda9e5e5 | 788e290ece1ec4bb4a2ba110b0ab2ffe1f451864 | 34 | 3 | 3 | 0 | SUPERSEDED_STAGE6 | use only allowlisted commits on stage6-v2 | NO | — |
| fix/naming-history-export-20260917 | 0b3795b7565c9432ee0419e2dfb8937fad56bdb0 | 503a2950ce3633ed491653b03c6d52c01859af5f | 272 | 50 | 49 | 1 | MIGRATION_ONLY_SOURCE | inventory only; do not merge branch | NO | archive-naming-history-20261003 |
## Recovery tag verification

These tags were fetched from origin and resolved to the following commits; none was moved or deleted:

- `pre-consolidation-main-20261003` → `788e290ece1ec4bb4a2ba110b0ab2ffe1f451864`
- `archive-naming-history-20261003` → `0b3795b7565c9432ee0419e2dfb8937fad56bdb0`
- `archive-scrapling-shadow-20261003` → `782e4ed277bd28a6cd7eb1caff2f91479f85fefa`
- `archive-export-foundation-20261003` → `dac6f8f394e3c29eb197fe7ebedfd029464f4517`
- `archive-sqlite-foundation-20261003` → `6e7c5940b90d2eec8ef6942c93c7af772af40b31`

## Deletion rule

Only a branch with `ahead_main == 0` and `unique_patch_count == 0` is a deletion candidate, and even then it remains `OWNER_CONFIRMATION_REQUIRED`. This audit never deletes a remote branch.
