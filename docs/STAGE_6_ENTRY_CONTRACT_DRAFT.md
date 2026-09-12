# Stage 6 Entry Contract Draft

状态：`DRAFT / NOT_FROZEN`  
版本：`stage6-entry-draft-v1`  
用途：供项目所有者讨论确认；不是历史事实，也不是生产授权。

## 1. 建议的 Stage 6 定位

`NEW_PROPOSAL`：Stage 6 定义为“人工审核后的候选晋升与受控 Apply 预演阶段”。它消费 Stage 5 已完成 disposition 的候选，生成可审计的 apply preview 和 rollback evidence；默认仍不直接执行生产写入。

这个定位让 Stage 5 保持纯候选层，并复用仓库已有 Dictionary Apply Gate，而不是让模型直接连接 Master。

## 2. Entry Gate

| 条款 | 来源标签 | 要求 |
| --- | --- | --- |
| Stage 4 正式放行 | `EXISTING_REQUIREMENT` | `FULL_STAGE4_RELEASE=true`，有独立 Acceptance |
| Stage 5 正式验收 | `INFERRED_REQUIREMENT` | `STAGE5_ACCEPTED`，不得以 shadow 状态进入 |
| 冻结身份一致 | `EXISTING_REQUIREMENT` | model、tokenizer、adapter、inference contract、Guard policy hash 完整一致 |
| 三批事实安全 | `EXISTING_REQUIREMENT` | factual/schema/category/technical-token escape 全为 0 |
| 人工审核闭环 | `INFERRED_REQUIREMENT` | 所有拟晋升候选都有真实人工 disposition，禁止模型伪造确认 |
| 只接收可晋升状态 | `NEW_PROPOSAL` | 仅 `ACCEPT_AS_IS` 与 `ACCEPT_WITH_MINOR_EDIT` 可进入 Apply preview |
| major/reject 隔离 | `NEW_PROPOSAL` | `REQUIRES_MAJOR_EDIT`、`REJECT`、`AMBIGUOUS` 不进入 Apply preview |
| 字段级审批 | `EXISTING_REQUIREMENT` | 一个字段通过不得隐式批准同 SKU 其他字段 |
| source_hash 新鲜度 | `EXISTING_REQUIREMENT` | 审核后源字段 hash 变化即自动失效，返回 Stage 5 |
| clean commit | `INFERRED_REQUIREMENT` | Acceptance、replay、preview 来自同一 clean commit 和 environment |
| replay/idempotency | `EXISTING_REQUIREMENT` | 相同输入和合同必须得到相同 candidate/apply preview hash |
| 生产写入默认关闭 | `EXISTING_REQUIREMENT` | 未单独授权前 `production_enabled=false` |
| 不影响事实层 | `EXISTING_REQUIREMENT` | 不改官网西语、Presence、Lifecycle、价格、availability |
| rollback evidence | `NEW_PROPOSAL` | apply preview 必须包含 before/after、目标文件 hash 和可恢复备份标识 |

## 3. 输入合同草案

`NEW_PROPOSAL`：每个输入字段至少包含：

- Stage 5 `candidate_id` 与 batch manifest hash；
- SKU、field、source_hash、Spanish source；
- final Chinese candidate；
- Guard policy hash 与 `accepted=true`；
- 人工 disposition、reviewer、reviewed_at；
- 当前生产字典/目标字段 hash；
- source_hash 复查结果。

任何缺失、hash 变化、Guard 非通过或人工状态不合法，都必须 fail closed。

## 4. 输出合同草案

`NEW_PROPOSAL`：Stage 6 只生成：

1. `stage6_apply_preview.jsonl/csv`；
2. `stage6_conflict_report.csv`；
3. `stage6_manifest.json`；
4. `stage6_replay_report.json`；
5. production write request（仅请求，不自动授权）。

输出必须逐字段说明目标、before、after、来源 candidate、审批证据和冲突原因。

## 5. 禁止项

- `EXISTING_REQUIREMENT`：不得修改官方西语事实、Presence、Lifecycle、价格或在售状态。
- `EXISTING_REQUIREMENT`：不得绕过 Dictionary Apply Gate。
- `EXISTING_REQUIREMENT`：不得把模型判断写成 `HUMAN_CONFIRMED`。
- `NEW_PROPOSAL`：不得把 Stage 6 当成新的翻译或训练阶段；遇到语言质量问题返回 Stage 5，遇到模型事实 P0 返回 Stage 4。
- `NEW_PROPOSAL`：不得“自动选择最新 adapter”或在同一批混用 policy。

## 6. 状态机草案

```text
NOT_FROZEN
  -> CONTRACT_FROZEN
  -> INPUT_CERTIFIED
  -> APPLY_PREVIEW_READY
  -> REPLAY_VERIFIED
  -> OWNER_APPROVED
  -> PRODUCTION_APPLY_SEPARATELY_AUTHORIZED
```

任何 Gate 失败都停在当前状态，不得静默 fallback。

## 7. 当前逐项结果

| 条款 | 当前结果 |
| --- | --- |
| Stage 4 正式放行 | FAIL |
| Stage 5 Acceptance | FAIL |
| Guard v2 replay | BLOCKED |
| 人工审核闭环 | BLOCKED（107 pending） |
| clean commit evidence | FAIL |
| 合同由项目所有者冻结 | BLOCKED |
| 生产写入保持关闭 | PASS |

当前结论：`NOT_READY_FOR_STAGE6`。
