# Stage 5 新源数据筛选记录（2026-09-13）

## 结论

从 2026-09-07 至 2026-09-12 的 PRIMARY products snapshot 中，按唯一
`(official_sku, localization_source_hash)` 选出 1,000 条源数据。它们不是旧训练集的重复行：

- 旧 train/validation/test 中的精确 `(SKU, source_hash)` 均排除；
- `NEW` 109 条；
- `SOURCE_HASH_CHANGED` 891 条；
- 选中集合与历史训练精确对重叠数为 0；
- 每条只有一个 `user` 消息，不含 assistant 标签，不可直接视为 Gold；
- 按 `run_id` 拆成 6 个批次，满足 Stage 5 的单批次身份合同。

## 选择规则

1. 来源必须来自正式 snapshot 的西语六字段：品名、一级类目、二级类目、规格、描述、产品详情。
2. 六字段不得为空、HTML、`null/undefined`、中文污染或“加入收藏”UI 文本。
3. 详情中存在商品编号时，必须与 SKU 一致。
4. `localization_source_hash` 必须由当前六字段重新计算。
5. 只允许 `NEW` 或 `SOURCE_HASH_CHANGED` 选择原因。
6. 不修改 Master、SQLite、字典；输出仍需逐条人工审核后才能建立新 train/validation/test split。

## 产物

主清单和每个批次的路径、SHA-256、数量见：

`runtime/training/qwen3_8b/20260913/stage5_new_source_snapshots/stage5_new_source_snapshots_manifest.json`

审计表：

`runtime/training/qwen3_8b/20260913/stage5_new_source_snapshots/stage5_new_source_snapshots_1000_audit.csv`

批次 JSONL 位于同目录的 `batches/` 子目录。每个批次已通过 `validate_input_rows()`。

## Stage 4 约束

Stage 4 审核报告位于：

`runtime/training/qwen3_8b/20260913/stage4_audit/STAGE4_DATA_AUDIT_20260913.md`

当前 Stage 4 仍不能正式 release：冻结 485 行评估中有 10 个 P0 和 1 个 P1；修复候选尚无正式 Gold；不同 closure 报告的 source-conflict SKU 清单还不一致。因此本批只允许 Stage 5 offline review/shadow，不允许训练或生产写入。
