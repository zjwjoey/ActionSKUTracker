# Stage 5 Remaining Work and Fix Plan

状态日期：2026-09-12  
原则：不重训、不重做模型推理、不覆盖旧批次、不写生产事实。

## 当前收口状态

以下自动项目已完成：Guard v2 固化、三批版本化 replay、幂等 hash 对账、clean worktree 完整 pytest（415 passed）和 v2 总结。旧 v1 产物保留在原目录；v2 产物使用 `*_guard_v2` 新目录。

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

### B1. 审核 108 条候选

来源：Batch 01 为 23 条，Batch 02 为 43 条，Batch 03 为 42 条。

方案：先完成 Guard v2 replay，再以 v2 Review Queue 为准逐条处理。人工状态只能是 `ACCEPT_AS_IS`、`ACCEPT_WITH_MINOR_EDIT`、`REQUIRES_MAJOR_EDIT`、`REJECT` 或 `AMBIGUOUS`。修改必须保留 reviewer、reviewed_at、原值和最终值。

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
