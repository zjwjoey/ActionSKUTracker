# Stage 4 / Stage 5 人工审核入口

这是一页总入口，解决审核文件分散的问题。所有文件都是只读候选/审核材料，不会自动写 Master、SQLite 或训练集。

## Stage 4：15 条阻断样本

- [15 条详细清单 CSV](F:/ActionSKUTracker/runtime/stage5/20260912/stage4_blocked_15_detailed_for_human_review.csv)
- [Stage 4 审核包说明](F:/ActionSKUTracker/runtime/stage5/20260912/stage4_human_review_package/README.md)
- [Stage 4 审核包 CSV](F:/ActionSKUTracker/runtime/stage5/20260912/stage4_human_review_package/stage4_blocked_15_detailed.csv)

其中 8 条为 `BLOCKED_STRICT`，7 条为 `BLOCKED_UPSTREAM`，阻断原因当前均记录为 `NUMERIC_DROPPED`，需要判断是源数据/规则问题还是模型事实问题。

## Stage 5：107 条候选

- [Stage 5 审核包说明](F:/ActionSKUTracker/runtime/stage5/20260912/stage5_human_review_package_guard_v2/README.md)
- [Batch 01：23 条 CSV](F:/ActionSKUTracker/runtime/stage5/20260912/stage5_human_review_package_guard_v2/batch_01_review.csv)
- [Batch 02：42 条 CSV](F:/ActionSKUTracker/runtime/stage5/20260912/stage5_human_review_package_guard_v2/batch_02_review.csv)
- [Batch 03：42 条 CSV](F:/ActionSKUTracker/runtime/stage5/20260912/stage5_human_review_package_guard_v2/batch_03_review.csv)
- [Batch 01 失败项](F:/ActionSKUTracker/runtime/stage5/20260912/stage5_human_review_package_guard_v2/batch_01_failures.csv)
- [Batch 02 失败项](F:/ActionSKUTracker/runtime/stage5/20260912/stage5_human_review_package_guard_v2/batch_02_failures.csv)
- [Batch 03 失败项](F:/ActionSKUTracker/runtime/stage5/20260912/stage5_human_review_package_guard_v2/batch_03_failures.csv)

每条 Stage 5 候选只能由真实人工填写：`ACCEPT_AS_IS`、`ACCEPT_WITH_MINOR_EDIT`、`REQUIRES_MAJOR_EDIT`、`REJECT` 或 `AMBIGUOUS`。

## 当前状态

- Stage 5 工程验证：已完成；Guard v2、replay、幂等和 clean pytest 均通过。
- Stage 5 正式结论：`RETURN_TO_STAGE4_REQUIRED`。
- Stage 6：`NOT_READY_FOR_STAGE6`。

