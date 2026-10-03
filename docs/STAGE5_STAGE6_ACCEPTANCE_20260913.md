# Stage 5 / Stage 6 Engineering Acceptance — 2026-09-13

本记录对应 `STAGE5_SOURCE_CANDIDATE_V2` 源版本候选队列和 `STAGE6_OFFLINE_PREVIEW_V1` 离线预览。它记录工程验收结果，不代表已经授权生产 Apply，也不代表已写入 Master 或 SQLite。

## Stage 5 source-version queue

- 选定候选：999 条；唯一 `(SKU, source_hash)`：999；重复：0。
- 五个合格来源 run：2026-09-07=9、2026-09-08=7、2026-09-09=108、2026-09-10=6、2026-09-12=869。
- 2026-09-11 被拦截的 5 条没有进入队列。
- 历史 exact source-pair overlap（截止 2026-09-13 选择时点）：0。
- snapshot exact-match：999；source hash 重算匹配：999。
- source version：`NEW_SKU_NEW_SOURCE` 103、`SOURCE_HASH_CHANGED` 896。
- prior SKU 已填 999/999；prior family 已填 999/999；冻结测试集 SKU 命中 17、family 命中 24。
- source consistency 已执行 999/999：`CONSISTENT` 824、`SOURCE_CONFLICT_REVIEW` 175。
- 生产写入：0；训练运行：0；当前仍是人工 Gold 审核队列。

审计结果：`PASS`。审计文件：
`runtime/training/qwen3_8b/20260913/stage5_new_source_versions_999/stage5_source_versions_v2_999_audit.json`。

说明：审计另行披露 956 条“之后生成的训练产物重叠”；这些产物生成时间晚于本队列的历史截止点，不计为选择时点的历史泄漏。

## Stage 6 offline preview

- owner signed 输入：103 条；接受/轻微修改：91 条；大修或存疑隔离：12 条。
- Apply Preview：91 行，其中 55 条 eligible（48 条 `WOULD_UPDATE`、7 条 `NO_CHANGE`），另有 36 条因 `SOURCE_CONFLICT` 被阻断。
- 独立 Conflict Report：48 行 = `SOURCE_CONFLICT` 36 条 + `NOT_OWNER_APPROVED` 12 条。它不是 55 条 eligible 的子集；eligible 55 条的 conflict class 为 0。
- source/review/target/policy hash freshness：全部通过；双次 replay 稳定；rollback preflight 通过；未静默覆盖源事实。
- 两次离线重放：91/91 行，冲突数 48/48，预览 hash 相同。
- production apply 授权：否；production writes：0；Master、SQLite、冻结 485 测试集均未改变。

预览目录：
`runtime/stage6/20260913/offline_preview_v1/`

核心状态：`REPLAY_VERIFIED`，范围为 `CERTIFIED_NONCONFLICTING_FIELDS_ONLY`。要进入生产 Apply，还需要明确授权并通过最终字段级审批门禁。

## Tests and protected-file verification

- Stage 6 / Stage 5 / Guard 定向测试：57 passed。
- 完整回归：453 passed。
- `runtime/master/Action_Master.xlsx` SHA-256：`ac281c58a05bf7c368288df7435256113e761743dda00606331972578ee157e6`
- `runtime/db/action_tracker.db` SHA-256：`c16b61c110bbd81c822b20adcae7d81f09facecb9524f2695e448d548d3f56f9`
- `runtime/training/qwen3_8b/20260911/stage4_test_only_485.jsonl` SHA-256：`74359269e9d75d030f402524ab5f7eec479239d53a29aeab3fef807c7785d232`

三份受保护文件哈希与预览前一致。随后按 Owner 授权执行了 SQLite Production Apply：严格写入 48 条 `WOULD_UPDATE + NONE` 字段，7 条 `NO_CHANGE` 未写入，blocked 48 条未触碰，没有 SKU 整行覆盖。

Production Apply 最终验收：48/48 reviewed value hash 回读匹配；48 个 blocked 字段值未变化；patch=48、patch events=144；Master、Dictionary、冻结测试集未变化；unexpected writes=0；before snapshot 与 SQLite rollback backup 完整可用。最终 after-audit：`SUCCESS`。

最终文件：

- `runtime/stage6/20260913/production_apply_v1/stage6_production_apply_manifest_final.json`
- `runtime/stage6/20260913/production_apply_v1/stage6_post_apply_audit_final.json`
- `runtime/stage6/20260913/production_apply_v1/rollback_artifact.json`
- `runtime/stage6/20260913/production_apply_v1/before_snapshot.sqlite`
- `runtime/stage6/20260913/production_apply_v1/full_regression_20260913.txt`

完整回归：453 passed。原始过程 manifest 曾在 SQLite 连接最终落盘前读取过一次 after-hash，已由独立最终审计生成的 reconciled manifest 校正；不影响实际字段验证结果。

## Post-Apply parity and Stage 6 activation

- SQLite → Master Export parity：PASS；SQLite CURRENT=5425、Master CURRENT=5425、SKU 集合一致。
- 除授权的 48 个中文字段差异外，意外 mismatch=0。
- Stage 6 正式生产合同已冻结：`config/stage6/stage6_production_contract.json`。
- 已整理 500 条新的 source-only Stage 6 Gold 审核候选：
  `runtime/stage6/20260913/source_candidate_500/`。
- 500 条均为 `CONSISTENT`、source hash 重算通过、无生产写入、无训练运行；下一步是生成中文 Gold、Guard 和 Owner review。

## Stage 6 Chinese Gold review preflight

- 已从 500 条 source-only 候选生成只读审核包：500 个 SKU、3000 个字段。
- 现有字典/规则安全关闭 1808 个字段；1170 个字段仍标记为模型或人工审核；22 个规则结果被 Guard 拒绝。
- 所有行均为 `PENDING_OWNER_REVIEW`，`training_eligible=false`；没有自动 Gold 晋级、生产写入或训练运行。
- 本轮未运行本地模型推理：当前环境 CUDA 不可用，Stage 5 合同禁止 CPU fallback；未用西语 fallback 冒充中文结果。
- 审核包：`runtime/stage6/20260913/gold_review_500/`，后续需在具备冻结推理环境后生成模型候选，再由 Guard 和 Owner 审核决定 Gold。
