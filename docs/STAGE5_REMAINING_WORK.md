# Stage 5 Remaining Work and Fix Plan

状态日期：2026-09-12  
原则：不重训、不重做模型推理、不覆盖旧批次、不写生产事实。

## 当前收口状态

以下自动项目已完成：Guard v2 固化、三批版本化 replay、幂等 hash 对账、clean worktree 完整 pytest（415 passed）和 v2 总结。旧 v1 产物保留在原目录；v2 产物使用 `*_guard_v2` 新目录。

## 2026-09-13：人工 Gold 接入（离线）

已将人工确认的 `Action_Stage5_候选数据_500条_修订版_446Gold.xlsx` 接入为不可变的 Stage 5 Gold 证据包。该步骤只写入 `runtime/training/.../stage5_gold_ingestion_446_v1/`，不写 Master、SQLite、正式字典或生产定位数据。

结果：

- 500 行、500 个唯一 SKU；446 条 `GOLD/ACCEPT_AS_GOLD`；45 条 `REVISE_THEN_GOLD`；9 条 `NOT_GOLD`。
- 与既有 train/validation/test 合并语料比对：442 条 Gold 已在历史 split 中，全部标记 `HISTORICAL_OVERLAP_BLOCKED`，不得重复加入新训练集。
- 4 条 Gold 不在既有 split 中，写入 `stage5_gold_disjoint_eligible_4.jsonl`，仅作为未来新 split 的候选，尚未启动训练。
- 工作簿与 Stage 5 500 候选包的六个西语事实字段和 `source_hash`：`0` 个不一致。
- 运行脚本再次执行时内容 hash 不变；现有 split 成员未改变。

证据 manifest：
`runtime/training/qwen3_8b/20260913/stage5_gold_ingestion_446_v2/stage5_gold_ingestion_446_v1.manifest.json`

入口脚本：`scripts/ingest_stage5_gold_workbook.mjs`。另外已用 Gold 的六个西语字段生成无答案的 source-only 输入，并通过现有 Stage 5 合同预检：446 行、2676 个字段计划、911 个模型请求计划，未把 assistant 答案送入输入；预检结果由 `scripts/run_stage5_pipeline.py --preflight-only` 输出，不产生候选或生产写入。

本次离线 Gold 接入已通过；Stage 5 正式发布仍受 Stage 4 `FULL_STAGE4_RELEASE=false` 门禁约束。该门禁必须在 Stage 4 独立闭环后才可解除，不能用人工 Gold 接入绕过。

## A. 必须修复后才能重新验收

### A1. 固化 Guard v2（已完成）

问题：Guard v1 把未绑定数值的中文字符和西语后缀误判为单位；三批的 17 次 unit reject 中包含误报。

完成证据：

1. 旧批次目录和 v1 manifest 保留；
2. 数值绑定单位识别修复及回归测试已提交；
3. policy 已冻结为 `stage5-hard-fact-guard-v2`；
4. 三批均复用已保存的 raw model output；
5. 三批 v2 目录和 manifest 已生成；
6. 三批 raw model output hash 未变化，unit reject 从 17 降为 1。

验收：PASS。误报大幅消除；真实数字/单位遗漏仍被拒绝；escaped factual error 保持 0。

### A2. 完成正式 replay/idempotency 报告（已完成）

问题：目前有稳定 ID 和不可变文件测试，但没有总级别 replay 证据。

结果：三批同一输入、记录输出、字典 manifest、合同和 Guard v2 连续执行两次；27 个文件 hash 全部一致。详见 `runtime/stage5/20260912/stage5_replay_report_guard_v2.json`。

### A3. 在 clean commit 上跑完整回归（已完成）

问题：三批 environment 显示 `git_worktree_clean=false`，当前相关修复也未提交。

结果：在 `F:\stage5-clean-20260912` 的 clean detached worktree、commit `5c24fee` 上完成完整 `pytest`，结果 `415 passed`。主工作区其他用户改动仍保留。

### A4. 解决 Stage 4 正式放行债务

问题：`FULL_STAGE4_RELEASE=false`，这是 Stage 5 正式 Acceptance 的硬阻断。

方案：回到 Stage 4 的既有 closure 流程处理，不在 Stage 5 修改训练集、Gold/Hard Test 或 adapter。需要独立 Stage 4 Acceptance 明确确认数字事实问题及银标边界已经闭环。

## B. 真正需要人工处理

### B1. 审核 Stage 5 当前候选（v2 为 107 条；v3 旁路包为 103 条）

来源：Guard v2 Batch 01 为 23 条、Batch 02 为 42 条、Batch 03 为 42 条；补齐确定类目映射后的 Resolver v3 旁路包为 23 + 40 + 40 = 103 条。历史 v1 的 108 条只作为谱系记录保留。

2026-09-12 owner-signed 包已覆盖 Resolver v3 的 103 条：77 条 `ACCEPT_AS_IS`、14 条 `ACCEPT_WITH_MINOR_EDIT`、11 条 `REQUIRES_MAJOR_EDIT`、1 条 `AMBIGUOUS`；其中 91 条可作为 Stage 6 候选，12 条继续隔离。该签字不解除 Stage 4 上游门禁。

方案：以审核者选定的版本化包为准逐条处理（默认 v3，或保留 v2 作为审计基线）。人工状态只能是 `ACCEPT_AS_IS`、`ACCEPT_WITH_MINOR_EDIT`、`REQUIRES_MAJOR_EDIT`、`REJECT` 或 `AMBIGUOUS`。修改必须保留 reviewer、reviewed_at、原值和最终值。

### B2. 冻结质量阈值

问题：仓库没有既有的人工质量比例标准。

方案：项目所有者确认 `STAGE_5_ACCEPTANCE.md` 中的 proposed threshold；在确认前不得称为冻结标准。安全硬门槛不变：所有事实错误逃逸必须为 0。

### B3. 冻结 Stage 6 定义

问题：Stage 6 没有正式定义。

方案：审阅 `STAGE_6_ENTRY_CONTRACT_DRAFT.md`，逐条接受、修改或拒绝；确认后转成版本化 JSON/MD contract 并加回归测试。在此之前不生成 Handoff。

## C. 后续增强，不阻塞当前文档收口

1. 增加更大规模且分别覆盖 `NEW`、`SOURCE_HASH_CHANGED`、`NEEDS_REVIEW` 的独立批次。
2. 将 aggregate manifest、failure/review 合并、replay 对账做成受测脚本。
3. 以字段拆分质量数据，避免 description/details 的问题被 name/spec 平均数掩盖。
4. 为 decimal comma、dimensions、duplicated numbers、model codes、technical tokens、品牌/颜色歧义、固定 cat1、语言残留、事实遗漏和 schema 损坏持续补历史 fixture。

## 当前剩余执行顺序

```text
A1/A2/A3（已完成）
  -> B1 人工审核 107 条
  -> A4 独立完成 Stage 4 Acceptance
  -> B2 冻结 Stage 5 质量阈值
  -> 重新签署 Stage 5 Acceptance
  -> B3 冻结 Stage 6 合同
  -> 才能生成 Stage 6 Handoff
```

其中 A1/A2 不需要重新训练，也不需要重新执行模型推理；只是使用现有 raw outputs 重新过正确的 Guard。
