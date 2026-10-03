# ActionSKUTracker — Qwen Stage 4 Final Closure Report

日期：2026-09-12（owner confirmation closure update）  
范围：离线证据与冻结测试审查；不写入生产数据。

## CURRENT STATE

| 项目 | 当前值 |
| --- | --- |
| branch | `feat/export-foundation-v1` |
| local HEAD | `33bf6f8a483d34ba5b319c1cba2995c64091f692` |
| worktree | `DIRTY`（存在本轮之前的用户修改；未清理、未覆盖） |
| origin branch HEAD | 本轮因 GitHub 连接经本机代理失败，无法验证；未将本地值冒充远端值 |
| base model | `runtime/models/Qwen3-8B`；config SHA-256 `f7c4eadfbbf522470667b797a3c89be2524832d2d599797248dc304fff447c30` |
| tokenizer | `runtime/models/Qwen3-8B/tokenizer.json`；SHA-256 `aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4` |
| current combined adapter | `combined_gold_incremental/formal_qlora_200_earlystop/adapter`；冻结 tree SHA-256 `dabc9294fa553d60eddb3a1a0c8939117b2bfefd8527d2f1742bdd6ccfbb1d0c` |
| field-conditioned adapter | `field_conditioned_v1/formal_qlora_200_earlystop/adapter`；冻结 tree SHA-256 `b239cb1c4ea553a5d012694b4e24a2caf1407c58e422b27a302855484f74988e` |
| inference contract | `stage4_inference_contract.json`；SHA-256 `a8d1328be1855e86e0461430911da6beedcdb3081a5dfc1e46b8b8793920720c` |
| frozen test | 485 rows；SHA-256 `74359269e9d75d030f402524ab5f7eec479239d53a29aeab3fef807c7785d232` |
| FULL_STAGE4_RELEASE | **false** |

## ROOT-CAUSE CERTIFICATION

按 `SOURCE → GOLD → SPLIT → CONTRACT → VALIDATOR/GUARD → MODEL` 顺序核验：

- 485 测试集保持不可变、`test_only=true`、`training_eligible=false`，无 split overlap。
- 10 条 P0 的西语源字段均有直接证据；不是旧数字计数器单独造成的表面误报。
- 13 条 owner-approved Gold 已确认；3006792、3224748 仍隔离为源冲突，不进入 Gold。
- 项目所有者已确认两份 AI Owner Ready 包：Silver 485/485 owner-confirmed（472 直接接受、12 修订后接受、1 条 3223907 源冲突隔离）；P0/P1 包 10 P0 + 1 P1 的根因认证已确认。原始工作簿不改写，确认记录为 `stage4_owner_confirmed_closure_20260912.json`。
- 当前合同要求保留同字段数字、单位、品牌/型号和产品对象；因此这些问题最终归类为 `CONFIRMED_MODEL_FAILURE`。
- 旧 `NUMERIC_DROPPED` 仅保留为历史 strict-audit 信号，当前 source numeric loss 为 0。

机器证据：`runtime/training/qwen3_8b/20260911/stage4_final_failure_certification.csv/json`；独立源字段核验：`stage4_full_eval_owner_signed_p0_triage_20260912.json`。

## 10 HARD FAILURES

| SKU | 字段 | failure | 根因 | confirmed model failure | remediation |
| --- | --- | --- | --- | --- | --- |
| 3219003 | name | Kate Legwear、40 Denier 丢失 | 模型输出字段忠实度不足 | YES | 不同 family 的品牌+型号/规格 Gold，优先 field-conditioned |
| 3220894 | description | 把详情中的 6 岁条件带入描述 | 跨字段事实迁移 | YES | 增加跨字段隔离 Gold 与 Guard |
| 3217699 | description | 增加无源 1.25L，并带入 2.5L | 数字幻觉/跨字段带入 | YES | 多字段数字隔离与“无源数字不得新增” Gold |
| 3223271 | description | H7、P21/5W、W5W、C5W、PY21W 丢失 | 技术 token 遗漏 | YES | 型号/IP/USB/功率等 token Gold |
| 3221705 | description | Milka 250g 丢失 | 数值遗漏 | YES | 单字段数量/重量 Gold |
| 3221795 | description | 14 个灯泡丢失 | 数量遗漏 | YES | 数量保持 Gold |
| 3201998 | description | 每卷 64 张丢失 | 数量遗漏 | YES | 包装数量 Gold |
| 3213267 | description | 27 件丢失 | 数量遗漏 | YES | 套装数量 Gold |
| 3009588 | description | 最多 5 升丢失 | 容量遗漏 | YES | 容量/上限表达 Gold |
| 3209605 | name | La Isla Living 商品误译为拖鞋垫 | 产品对象语义错误 | YES | 不同 family 的产品对象对比 Gold |

## RETRAIN DECISION

`TARGETED_RETRAIN_REQUIRED`

原因：10 条已完成源字段级根因认证，属于确认的模型输出问题。尚未开始训练；不得使用冻结 485 或同 SKU/family 样本构造训练数据。

## TARGETED RETRAIN STATUS

| 项目 | 状态 |
| --- | --- |
| remediation dataset | 已完成 200 条 disjoint、source-verified 候选的两轮模型审校；仍需要人工确认 Gold |
| rows | 0 条可训练 Gold；最终人工包 200 条模型审校通过待 owner 确认；原始两批候选共 65 条 Guard 拦截/拒绝 |
| family isolation | 待构建时验证 |
| adapter type | 优先 field-conditioned；不默认重训六字段整行 |
| Round 1 | 未执行 |
| Round 2 | 未执行 |
| selected checkpoint | 无 |

最多两轮；每轮只改变 remediation Gold，保持 base/tokenizer/contract/evaluator/Guard 不变。

初始 50 条队列保留为历史证据。v4 选出 200 条定向修复候选后完成两轮 DeepSeek 审校与本地 Guard：152 条通过、48 条拦截。随后从 disjoint v5 补充池再审校，按五类各补足缺口，最终形成 200 条 `APPROVED_MODEL_REVIEWED` 的人工确认包；另有 35 条合格备用，所有原始拦截/拒绝记录均保留。最终包：
`runtime/training/qwen3_8b/20260911/stage4_remediation200_final_owner_review_20260912.xlsx`。
200 条均已排除 Frozen 485、历史训练/验证/测试、硬测试集和 P0 完全相同的西语一级/二级类目组合，仍是 Silver，未获 owner 确认，不得进入训练。
外部审核表另给出 177 条 `ACCEPT_AS_GOLD`，项目所有者已在本次对话确认使用。进一步泄漏审计发现 177 条全部已出现在旧 train/validation/test 语料（不与冻结 Stage 4 485 测试重叠，但与历史字段语料重叠），因此已确认但被标记为 `HUMAN_CONFIRMED_REMEDIATION_GOLD_LEAKAGE_BLOCKED`，训练资格为 0，不得直接训练。

## FROZEN 485 RESULTS（当前 adapter）

| 指标 | 结果 |
| --- | ---: |
| hard fact | **10 P0**（另 1 P1） |
| JSON parse | 100% |
| field schema | 100% |
| required non-empty | 100% |
| cat1 valid | 100% |
| numeric preservation | 99.24399% |
| numeric hallucination | 0.20619% |
| Spanish residual | 0.10309% |

尚无 after 结果，因为 targeted retrain 尚未执行。旧数字表面差异（重复值合并、`dos/cuatro` 转中文数字、单位表达）已与 P0 分开。

## SILVER COVERAGE

| 项目 | 数值 |
| --- | ---: |
| total | 485 |
| reviewed as owner-confirmed package | 485 |
| pending | 0（本次 owner confirmation 已覆盖审核包；原始工作簿签字列保持不变） |
| disposition | 472 direct accept / 12 revise-then-accept / 1 source-conflict-exclude |
| source conflict | 3223907（Silver 包内）及 3006792、3224748（独立 Stage 4 源冲突）均隔离，不进入 Gold |

当前合同要求的 485 覆盖已由项目所有者对 Owner Ready 包作出明确确认；该确认只记录处置与证据，不把未确认的 remediation candidate 自动晋升为 Gold。

最新 AI Owner Ready 包已完成辅助处置：472 条建议直接确认、12 条建议修订后确认、1 条建议源冲突隔离；项目所有者已在本次对话明确确认该包。复核报告：`runtime/training/qwen3_8b/20260911/stage4_ai_owner_ready_recheck_20260912.json`；确认记录：`runtime/training/qwen3_8b/20260911/stage4_owner_confirmed_closure_20260912.json`。

已生成完整人工审核包：

- `runtime/training/qwen3_8b/20260911/stage4_485_silver_review_package.csv`
- `runtime/training/qwen3_8b/20260911/stage4_485_silver_review_package.xlsx`
- `runtime/training/qwen3_8b/20260911/stage4_silver_coverage_report.json`

Excel 审核包共 486 行（含表头）、20 列，首行冻结并启用筛选；485 条数据均为 `human_disposition=PENDING`、`gold_status=MODEL_REVIEWED_SILVER_NOT_HUMAN_GOLD`。

## REGRESSION

`437 passed`（完整 pytest）。本轮新增审计脚本已通过 `py_compile`；未发生 Master、SQLite、生产字典或旧模型覆盖。

## STAGE 4 GATES

| Gate | 结果 |
| --- | --- |
| 13 owner-approved Gold | PASS |
| 3006792/3224748 source conflict isolation | PASS |
| old NUMERIC_DROPPED current-blocker correction | PASS |
| frozen 485 JSON/schema/non-empty/category | PASS |
| split/leakage | PASS |
| train/infer parity | PASS（冻结 contract 与评估路径一致） |
| Hard Fact = 0 | **FAIL**（10 P0） |
| P1 disposition | BLOCKED（尚未按合同处置） |
| Silver coverage | **PASS（485/485 owner-confirmed package）** |
| clean commit | FAIL（worktree dirty） |
| full regression | PASS |
| FULL_STAGE4_RELEASE | **FALSE** |

## STAGE 4 VERDICT

`STAGE4_BLOCKED`  
`FULL_STAGE4_RELEASE=false`

## STAGE 5 FINAL GATE

`RETURN_TO_STAGE4_REQUIRED`。Stage 4 尚未正式放行，不能把现有 Stage 5 候选升级为正式 Acceptance。Stage 5 的 Guard v2、Resolver v3 和 owner disposition 仍应保留，但不越过上游 Gate。

## STAGE 6 READINESS

`NOT_READY_FOR_STAGE6`

## 人工下一步

1. 针对已确认的 10 条模型 P0，审核/确认最终 200 条模型审校通过的定向修复候选中哪些可形成 disjoint remediation Gold；候选包仍是 Silver，`training_eligible=0`。
2. 按最多两轮 targeted retrain 规则执行（不得使用冻结 485 或同 SKU/family 泄漏样本），然后重跑同一 485 冻结测试。
3. 只有 Hard Fact = 0、Guard/评估回归通过且工作区收口后，才允许设置 `FULL_STAGE4_RELEASE=true` 并签署 `STAGE_4_ACCEPTANCE`。
