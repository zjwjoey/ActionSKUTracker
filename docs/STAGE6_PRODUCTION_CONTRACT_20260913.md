# Stage 6 Production Contract — 2026-09-13

Stage 6 已从历史的离线 Preview 合同进入正式的受控字段 Apply 合同。历史 Preview 合同保持不变，用于解释和验证 2026-09-13 之前生成的预览证据；生产入口使用：

`config/stage6/stage6_production_contract.json`

冻结约束：

- 目标仅为 SQLite PRIMARY 的中文字段级投影。
- 只允许 `name / cat1 / cat2 / spec / description / details`。
- 必须使用冻结的 eligible CSV，并要求 Owner 运行时授权。
- 每个字段写入前重新校验 source、review、target、policy freshness。
- 必须单事务、before snapshot、SQLite rollback backup 和字段级 patch 事件。
- Master Excel、Dictionary 文件不属于本合同写入目标。
- 禁止 SKU 整行覆盖，禁止写入 blocked conflict report。
- Apply 后必须逐字段回读 reviewed value hash，并完成 parity 和完整回归。

2026-09-13 的首个生产 Apply 已按该约束完成：48 条更新、7 条 no-change、0 条 unexpected writes。Stage 6 后续数据入口为 `runtime/stage6/20260913/source_candidate_500/`，该目录仅是 source-only Gold 审核队列，不具备生产写入或训练授权。

随后生成的中文 Gold 预审包位于 `runtime/stage6/20260913/gold_review_500/`。该包只保留规则安全闭合值，未闭合字段明确标记为模型/人工审核；500 个 SKU 均保持 `PENDING_OWNER_REVIEW`，不构成 Apply 授权。
