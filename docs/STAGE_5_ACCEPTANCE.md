# Stage 5 Acceptance Report

审计日期：2026-09-12  
仓库：`F:\ActionSKUTracker`  
分支：`feat/export-foundation-v1`  
审计初始 HEAD：`79b3942144f3da5ec731cf91e7b9059c1e5c584d`  
最新封板文档 HEAD：`33e4b33`（replay 实际执行 commit：`5c24fee`）  
远端分支 HEAD：`1dc2fc0338c2b6ace73b4869b11a0ebfd1179c35`  
报告性质：Stage 5 离线 shadow 总验收；不授权生产写入、训练或 Stage 6

## CURRENT REALITY

Stage 5 已经形成可运行的离线候选流水线：正式增量输入校验、字段所有权、字典优先、冻结 Qwen 推理、严格 JSON 解析、事实 Guard、候选 provenance、不可变批次产物、人工审核队列和失败报告均已有实现与测试。

当前不具备正式 Acceptance 条件。上游 Stage 4 状态只允许 `READY_FOR_STAGE5_OFFLINE_SHADOW`，`FULL_STAGE4_RELEASE=false`，且没有独立通过的 Stage 4 Acceptance。三批 Stage 5 产物均来自同一 commit 和环境，但生成时 worktree 不干净，使用的是 Guard v1。随后真实失败审查发现 Guard v1 存在单位误报，当前本地已形成 Guard v2 修复但尚未对原始输出执行版本化重放。

生产边界保持安全：三份 manifest 中 SQLite PRIMARY、Master、production dictionary、production localization、Lifecycle、价格和 availability 写入均为 `false`。

## STAGE 5 TARGET ARCHITECTURE

```text
Formal committed source
  -> Change Selector (NEW / source_hash changed / NEEDS_REVIEW / resolver gap)
  -> Field Ownership Resolver
  -> Deterministic Dictionary Resolver
  -> unresolved field gap only
  -> frozen Qwen adapter
  -> strict single-field JSON parser
  -> numeric / unit / technical-token / category / language / schema Guard
  -> provenance builder
  -> immutable isolated candidate store
  -> independent human Review Queue
  -> separately authorized Apply Gate (not part of Stage 5)
```

模型只能处理 `name`、`spec`、`description`、`details` 的真正语言缺口。`cat1`、`cat2`、品牌、单位、数字事实和技术 token 由确定性规则及 Guard 管理，模型没有事实改写权。

## FOUND GAPS

### P0

1. Stage 4 正式放行缺失：`FULL_STAGE4_RELEASE=false`。按冻结合同，Stage 5 不能越过该条件签署正式 Acceptance。
2. 没有独立、已签署的 Stage 4 Acceptance；当前 485 条测试数据仍是模型复核银标，不是人工 Gold。

本轮三批没有发现事实错误逃逸 Guard，因此没有新增的 Stage 5 模型逃逸型 P0。

### P1

1. 初始 v1 记录为 `108` 条；Guard v2 replay 的当前基线为 `107` 条 `PENDING`，Resolver v3 旁路重放为 `103` 条。三者均不得冒充人工确认。
2. 三批由 Guard v1 生成；其单位识别会把“巧克力”的“克”、“安装”的“安”及西语后缀误判为单位，导致部分 `UNIT_REJECT` 是误报。
3. Guard v2 已通过定向测试，但尚未用已保存的模型输出做版本化重放，因此当前批次报告不能代表 v2 最终分类。
4. 正式 replay/idempotency 报告尚未完成；现阶段仅有不可变写入及稳定 ID 的单元测试证据。
5. 三批虽绑定同一 commit `79b3942` 和相同环境 manifest，但 `git_worktree_clean=false`，不满足“同一 clean commit”验收条款。
6. 当前 Guard v2 及其回归测试仍在工作区，尚未形成独立 commit；旧批次 manifest 仍正确绑定 v1，不得覆盖。

### P2

1. 批次样本量为每批 10 SKU，只能证明工程链路，不能代表长期业务质量。
2. 现有三个输入批次均以 `NEEDS_REVIEW` 进入；尚未分别覆盖真实 `NEW` 和 `SOURCE_HASH_CHANGED` 的正式批次分布。
3. 仓库中未冻结人工质量比例阈值。
4. 需要把总 manifest、aggregate summary、replay report 的生成过程固化为脚本，避免手工汇总。

## IMPLEMENTED FIXES

1. `config/stage5/stage5_input_contract.json`：限制增量输入范围，禁止 assistant/reference target 混入输入。
2. `config/stage5/stage5_field_ownership.json`：冻结字段事实来源、模型权限、Guard、fallback、审核与写入权限。
3. `config/stage5/stage5_pipeline_contract.json`：绑定 base model、tokenizer、adapter、Stage 4 inference contract、prompt、chat template 和 greedy generation。
4. `config/stage5/stage5_guard_policy.json`：建立拒绝型事实 Guard；当前工作区已升为 v2，单位只在绑定数值时识别。
5. `src/action_tracker/stage5/pipeline.py`：实现输入验证、冻结身份校验、规则优先规划、确定性 ID、候选 provenance、失败归因、Review Queue 和不可变原子产物。
6. `scripts/build_stage5_batch.py`：从三份独立正式来源构建互不重叠的 source-only 批次。
7. `scripts/run_stage5_pipeline.py`：执行冻结模型或读取已记录模型输出；没有 CPU fallback，不连接生产写入。
8. `src/action_tracker/translation/model_guard.py`：补齐数字、单位、技术 token、品牌/颜色、语言和 schema 事实保护，并修复单位误报。

## TEST RESULTS

| 范围 | 结果 | 说明 |
| --- | ---: | --- |
| Guard + Stage 5 pipeline + legacy offline Stage 5 | `59 passed` | 2026-09-12，Guard v2 本地代码 |
| Stage 5 三批执行 | `3 completed` | 每批均有独立 manifest、evaluation、candidate、failure、review、environment、requests、outputs |
| 生产写入检查 | PASS | 三批 manifest 的全部生产写入字段均为 false |
| Candidate duplicate | PASS | 三批均为 0 |
| Pipeline error | PASS | 三批均为 0 |
| 事实错误逃逸 Guard | PASS | 三批所有 escaped 指标均为 0 |
| Replay | BLOCKED | 尚未基于 Guard v2 对记录输出完成版本化重放 |
| 完整仓库回归 | NOT RUN FOR FINAL STATE | 最近一次仅执行 59 项定向测试 |

## THREE BATCH RESULTS

| 指标 | Batch 01 | Batch 02 | Batch 03 | 合计 |
| --- | ---: | ---: | ---: | ---: |
| observation date | 2026-09-10 | 2026-09-11 | 2026-09-12 | 3 个独立来源日 |
| eligible SKU | 10 | 10 | 10 | 30 |
| NEW | 0 | 0 | 0 | 0 |
| source hash changed | 0 | 0 | 0 | 0 |
| NEEDS_REVIEW | 10 | 10 | 10 | 30 |
| 字段数 | 60 | 60 | 60 | 180 |
| resolver coverage | 37 | 17 | 18 | 72 |
| resolver gap | 23 | 40 | 40 | 103 |
| model invocation | 23 | 40 | 40 | 103 |
| model invocation ratio | 38.33% | 66.67% | 66.67% | 57.22% |
| Guard pass | 21 | 33 | 32 | 86 |
| Guard reject | 2 | 7 | 8 | 17 |
| numeric reject | 0 | 1 | 0 | 1 |
| unit reject | 2 | 7 | 8 | 17 |
| tech-token reject | 0 | 0 | 0 | 0 |
| category reject | 0 | 0 | 0 | 0 |
| residual-language reject | 0 | 0 | 0 | 0 |
| schema reject | 0 | 0 | 0 | 0 |
| human accept/minor/major/reject | 0/0/0/0 | 0/0/0/0 | 0/0/0/0 | 尚未人工裁决 |
| human pending / unresolved（v1 初始记录） | 23 | 43 | 42 | 108 |
| duplicate | 0 | 0 | 0 | 0 |
| pipeline error | 0 | 0 | 0 | 0 |
| factual error escaped | 0 | 0 | 0 | 0 |

注：Guard v1 的 unit reject 不能直接解释为模型错误；其中已确认存在规则误报。原始模型输出和旧报告必须保留，后续只允许输出到新版本目录重放，不得覆盖本表所引用证据。

## FAILURE CLOSURE

- `UNKNOWN`：Stage 6 的正式定义、正式人工质量阈值和 Guard v2 重放后的真实失败分布仍未知。
- `P0`：没有 Stage 5 错误逃逸；但 Stage 4 正式放行缺失仍是上游 P0 准入阻断。
- `human review`：以最新 v2 包为准为 107 条 `PENDING`；Resolver v3 旁路包为 103 条。
- 已拦截候选保持隔离，没有进入安全候选或生产事实层。
- 旧单位误报根因属于 validator/Guard，而不是 Qwen；不得错误归因给模型。

## PROPOSED_STAGE5_QUALITY_THRESHOLD

仓库没有冻结现成阈值。以下仅为需要项目所有者确认的新合同建议：

1. 三个验收批次必须 100% 完成人工 disposition，不允许 `PENDING`。
2. factual/schema/category/technical-token 错误逃逸必须始终为 0。
3. `REQUIRES_MAJOR_EDIT` 与 `REJECT` 可以存在，但必须隔离且不得进入 Apply；同时应按字段报告比例，不用平均数掩盖单字段失败。
4. 在积累至少三个更大规模独立批次前，不设未经证据支持的模型通过率数字。

## STAGE 5 GATES

| Gate | 结果 | 证据/原因 |
| --- | --- | --- |
| 三个独立批次 | PASS | 三个不同 observation day，SKU 互不重叠 |
| 独立 batch manifest | PASS | 三份 manifest 均存在 |
| 合法增量输入 | PASS | 30 SKU 均为 NEEDS_REVIEW，source-only |
| Dictionary Resolver 优先 | PASS | 72/180 字段由 resolver 闭合 |
| Qwen 仅处理允许字段 gap | PASS | cat1/cat2 不允许模型处理 |
| 冻结模型身份 | PASS FOR SHADOW | 三批 model/tokenizer/adapter/Stage4 contract hash 一致 |
| 当前 Guard policy 与批次一致 | FAIL | 三批是 v1；本地当前为 v2 |
| Candidate provenance | PASS | 每字段候选包含来源、hash、模型与 Guard 证据 |
| 自动 P0 factual escape = 0 | PASS | 三批均为 0 |
| schema failure = 0 | PASS | 三批均为 0 |
| duplicate = 0 | PASS | 三批均为 0 |
| unexplained failure = 0 | BLOCKED | v1 单位误报尚未经 v2 重放闭环 |
| unresolved P0 = 0 | PASS IN STAGE5 | 无 Stage 5 escape P0 |
| 无生产写入 | PASS | 所有 manifest 写入开关均为 false |
| replay/idempotency | BLOCKED | 缺正式重放报告 |
| Review Queue 独立处理 | PASS STRUCTURE / BLOCKED CONTENT | 文件完整，但 108 条待人工处理 |
| 同一 clean commit/environment/policy | FAIL | 同环境、同 commit，但 dirty；当前 policy 已变化 |
| Stage 4 frozen artifacts 未改 | PASS | base/tokenizer/adapter/Stage4 contract hash 一致 |
| Stage 4 full release | FAIL | `FULL_STAGE4_RELEASE=false` |
| 完整测试 | BLOCKED | 只完成 59 项定向测试，最终全量回归未跑 |

## STAGE 5 VERDICT

`RETURN_TO_STAGE4_REQUIRED`

原因不是三批出现事实错误逃逸，而是 Stage 5 正式 Acceptance 明确要求上游 Stage 4 full release；当前该 Gate 为 false。现阶段 Stage 5 只能继续作为安全的离线 shadow 候选流水线。

## STAGE 6 DEFINITION STATUS

`STAGE_6_DEFINITION_STATUS = NOT_FROZEN`

仓库中没有发现正式 Stage 6 定义或已签署 Handoff。详见 `docs/STAGE6_DEFINITION_AUDIT.md`。

## STAGE 6 ENTRY GATE

当前只能使用 `docs/STAGE_6_ENTRY_CONTRACT_DRAFT.md` 作为讨论草案。至少要求：Stage 4 正式放行、Stage 5 Acceptance 通过、Guard v2 重放与全量回归通过、人工审核闭环、clean commit 证据、候选与生产 Apply Gate 保持物理解耦。

## STAGE 6 VERDICT

`NOT_READY_FOR_STAGE6`

阻断项（初始审计记录）：Stage 4 未正式放行、Stage 5 未通过、108 条人工审核未闭环、v2 replay 未完成、工作区不干净、Stage 6 合同尚未由项目所有者冻结。最新状态以本页 V2 CLOSURE ADDENDUM 及 `docs/STAGE5_EXTERNAL_REVIEW_RECONCILIATION.md` 为准。

## 2026-09-12 V2 CLOSURE ADDENDUM

本节为本报告的最新状态，若与上文的初始 shadow 记录冲突，以本节为准。旧 v1 批次目录、manifest 和报告均保留，没有被覆盖。

### 已自动完成

- Guard v2 已冻结并提交：`46f4b3d`；空可选 `cat1` 校验修复提交：`723bd4b`。
- 依赖的 Qwen/Stage4 工具已纳入 Git，clean worktree 可完整运行测试：`5c24fee`。
- 三批均使用原有 `stage5_model_outputs.jsonl` 做 v2 replay；没有重新推理。
- 三批 v2 结果：Batch 01 `22 pass / 1 reject / 23 pending`；Batch 02 `39 pass / 1 reject / 42 pending`；Batch 03 `40 pass / 0 reject / 42 pending`。
- v2 合计：180 字段、73 resolver coverage、103 model invocation、101 Guard pass、2 Guard reject、1 numeric reject、1 unit reject、107 human pending、6 failure、0 duplicate、0 pipeline error、0 factual escape。
- 幂等 replay：27 个 v2 产物文件第二次执行后 `changed_files=0`。
- clean worktree 完整 pytest：`415 passed`。

### V2 gates

| Gate | V2 结果 |
| --- | --- |
| Guard v2 与批次一致 | PASS |
| 版本化 replay | PASS（使用记录输出的后推理 replay） |
| idempotency | PASS |
| clean worktree full pytest | PASS（415 passed） |
| production write boundary | PASS |
| human review closure | BLOCKED（107 pending） |
| Stage 4 full release | FAIL（仍为 false） |

### V2 verdict

`RETURN_TO_STAGE4_REQUIRED`

Stage 5 的工程性问题已经自动收口；当前唯一上游硬阻断仍是 Stage 4 `FULL_STAGE4_RELEASE=false`。同时 107 条候选必须由真实人工完成 disposition 后，才可能讨论 Stage 5 的业务质量接受。

## 2026-09-12 外部复核与 Resolver v3 对账

外部辅助复核提出的 15 条 Stage 4、107 条 Stage 5 和 Stage 6 合同意见已单独记录在 `docs/STAGE5_EXTERNAL_REVIEW_RECONCILIATION.md`。该意见不等同于人工签字。

### V3 Owner-Signed Closure Addendum — 2026-09-12

项目所有者提交的 `Qwen_Stage4_Stage5_OWNER_SIGNED_20260912.xlsx` 已完成证据核验：Stage 4 的 13 条可用 Gold 候选为 `APPROVED + OWNER_SIGNED_OFF`，3006792 与 3224748 保持 `HOLD_SOURCE_CONFLICT`；Stage 5 Resolver v3 的 103 条均已填写 owner disposition，91 条具备后续 Stage 6 候选资格。该包只关闭审核处置队列，不等同于 Stage 4 全量发布验收。

对应证据报告：`runtime/training/qwen3_8b/20260911/stage4_stage5_owner_signed_recheck_20260912.json`。当前 `FULL_STAGE4_RELEASE` 仍为 `false`，因为 500 条 strict closure 中仍有 485 条 `TEST_ONLY_MODEL_REVIEWED_SILVER`，不能被 13 条样本签字替代。

对三条确定的类目字典映射补齐后，使用原始输入与已记录模型输出完成旁路 Resolver v3 replay：`resolver coverage=77`、`human pending=103`、`failure_count=2`，没有重新推理，也没有覆盖 v2 的 107 条基线。v3 审核包位于 `runtime/stage5/20260912/stage5_human_review_package_resolver_v3/`。

## 2026-09-13 人工 Gold 接入后最新状态

项目所有者提供的 `Action_Stage5_候选数据_500条_修订版_446Gold.xlsx` 已按 Stage 5 离线合同接入。该包不是对旧 103/107 条候选的自动覆盖，而是新的、可追溯的人工作业证据版本。

- 500 行、500 个唯一 SKU；`GOLD/ACCEPT_AS_GOLD=446`；`REVISE_THEN_GOLD=45`；`NOT_GOLD=9`。
- 446 条 Gold 中 442 条已存在于历史 train/validation/test split，已标为 `HISTORICAL_OVERLAP_BLOCKED`，不可重复加入新训练集；4 条不在历史 split，可作为未来新 split 候选：`3215954、3218150、3221215、3221470`。
- 与 Stage 5 500 候选包的六个西语事实字段和 `source_hash` 对账均为 0 mismatch。
- 生成的 source-only 输入严格只含一条 `user` 消息，不含 assistant/reference 答案；现有 Stage 5 预检通过：446 行、2676 个字段计划、911 个模型请求计划。
- 证据目录：`runtime/training/qwen3_8b/20260913/stage5_gold_ingestion_446_v2/`；入口脚本：`scripts/ingest_stage5_gold_workbook.mjs`。
- 系统 Python 没有 CUDA，冻结合同禁止 CPU fallback；项目专用 CUDA 12.6 虚拟环境可用，因此没有用 Gold 答案伪造模型输出，而是通过项目虚拟环境执行真实 shadow。
- 追加验证：项目专用 CUDA 12.6 虚拟环境已对 4 条无历史重叠 Gold 执行真实 shadow，共 16 个模型字段请求；16/16 Guard PASS、事实逃逸 0、pipeline error 0。与人工 Gold 比较 11 条完全一致、13 条不一致，比较报告为 `runtime/stage5/20260913/gold_shadow_disjoint4_qwen_v1/stage5_shadow_gold_comparison.json`，因此 4 条仍保持人工复核，不得自动 Apply。

全量追加验证：446 条 Gold 已完成真实冻结模型 shadow，共 2676 个字段计划、911 个模型请求；852 Guard PASS、59 Guard REJECT、0 事实逃逸、0 pipeline error、0 duplicate。与人工 Gold 对账为 1630 条完全一致、1046 条需复核，报告为 `runtime/stage5/20260913/gold_shadow_full_qwen_batch32_v1/stage5_shadow_gold_comparison.json`；全量仍为 `REVIEW_REQUIRED_NO_APPLY`。

该步骤关闭了本批 Gold 的接入和泄漏审计，但不解除 Stage 4 `FULL_STAGE4_RELEASE=false`，也不自动授权训练、Master、SQLite、正式字典或 Stage 6。生产写入开关仍全部为 `false`。
