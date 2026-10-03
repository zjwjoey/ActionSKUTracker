# Stage 4 / Stage 5 外部复核对账与待签包说明

生成日期：2026-09-12  
性质：辅助复核记录，不等同于人工签字，不授权训练、字典 Apply 或生产写入。

## 1. 复核输入与边界

本文件对照了外部辅助复核文本、仓库内不可变的 Stage 4 / Stage 5 产物和当前字典。外部文本中的 `ACCEPT_AS_IS`、`MINOR`、`MAJOR`、`RESOLVER_FIX`、`AMBIGUOUS` 只作为建议，不能写成 `HUMAN_CONFIRMED`。正式状态仍以审核者在审核包中填写的 disposition 为准。

对账基线：

- Stage 4 阻断包：15 条，位于 `runtime/stage5/20260912/stage4_human_review_package/`。
- Stage 5 Guard v2 原始审核包：23 + 42 + 42 = 107 条，位于 `runtime/stage5/20260912/stage5_human_review_package_guard_v2/`。
- Stage 5 Resolver v3 旁路重放包：23 + 40 + 40 = 103 条，位于 `runtime/stage5/20260912/stage5_human_review_package_resolver_v3/`。
- Stage 6 合同仍为草案，未因本次复核自动冻结。

## 2. 15 条 Stage 4 对账结论

重新使用当前数字事实计数器逐字段核验 `name / spec / description / details` 后，15 条均未发现源数字事实丢失。小数逗号与小数点按同一数值比较。`2566006` 和 `3222290` 各有一处数字表面计数差异，分别来自把西语文字 `dos`、`cinco` 规范化为阿拉伯数字；这不是新增商品事实，但已在 Closure Recheck 中单独记录。因此，原先的 `NUMERIC_DROPPED` 不能直接解释为当前中文候选的真实数字丢失，也不应据此 broad retrain。

当前仍需人工处理的不是“自动改成 Gold”，而是：

- `3006792`：西语描述写 1 节 AAA，详情写 3 节 AAA，属于源字段冲突。
- `3224748`：西语描述含 `3000 k`，规格为 300 流明，详情又出现 Lumen 3000，属于源字段冲突。
- 其余 13 条可作为文本/品牌/系列名修订候选，但仍需审核者确认后才能成为 Gold。

详细逐条文件：`runtime/stage5/20260912/stage4_human_review_package/stage4_blocked_15_detailed.csv`。冲突摘要：`runtime/stage5/20260912/stage4_source_conflicts.csv`。Stage 4 Strict Test Only 版本化纠正记录：`runtime/training/qwen3_8b/20260911/qwen_incremental_stage4_release500_strict_test_only_audit_closure_recheck.json`。

## 3. 108 → 107 的谱系解释

通过 `(sku, field)` 对比旧 v1 队列和 Guard v2 队列：

- v1：108 条；v2：107 条；
- 唯一消失的键：`2558074 / cat2`；
- v1 将源值 `Chocolate` 的该字段记为 `UNIT_HALLUCINATED` 并进入 pending；
- Guard v2 改为仅检查“数字绑定的度量单位”，该记录在当前规则下被安全接受，因此不再是人工 pending；
- 没有新增键，没有静默丢弃，也不是去重误删。

因此历史 Acceptance 文档中保留的 108 是初始审计记录，当前正式 v2 基线是 107。Resolver v3 之后的 103 是另一份旁路重放结果，原因是补齐了 4 条确定的类目字典映射，不能反写覆盖 v2 的 107 基线。

## 4. 外部对 Stage 5 的建议（未签字）

外部复核建议分布为：77 条 `ACCEPT_AS_IS`、14 条 `ACCEPT_WITH_MINOR_EDIT`、11 条 `REQUIRES_MAJOR_EDIT`、4 条 `RESOLVER_FIX`、1 条 `AMBIGUOUS`。这些数字已保存为辅助建议清单，但不是系统审核结果。

逐字段建议清单（11 + 14 + 4 + 1 = 30 条被点名记录）：`runtime/stage5/20260912/stage5_external_review_disposition_suggestions.csv`；剩余 77 条 `ACCEPT_AS_IS` 只保留汇总计数，未伪造逐条确认。

三份下载文件的机器核对结果：`runtime/stage5/20260912/assistant_final_review_audit.json`。

外部点名的高风险示例包括：`3224146/description`（候选增加“外层酥脆”）、`3224907/description`（蝴蝶误译为蝴蝶结）、`3225230/description`（玻璃误写为水晶）、`3206734/name`（雪景球对象误译）等。它们仍在审核/隔离范围内，未进入 Master 或正式字典。

`3225667/name` 因源字段出现女童/女性不一致，保留 `AMBIGUOUS` 建议，需核源后再决定。

## 5. Resolver / 字典的确定性修复

当前正式 `data/dictionary/category_dictionary.csv` 已补齐三条已有证据支持的映射，并同步更新 `baseline_manifest.json`：

| 西语一级/二级 | 中文一级/二级 |
| --- | --- |
| Cuidado personal / Maquillaje | 个人美容 / 彩妆 |
| Comer y beber / Galletas | 食品饮料 / 饼干 |
| Comer y beber / Alimentación | 食品饮料 / 食品 |

使用相同输入和已记录模型输出进行 Resolver v3 离线重放后，4 条 `cat2` Resolver Failure 消失，pending 从 107 降为 103，失败数从 6 降为 2。v2 包和所有模型输出均保留；v3 只是旁路验证，仍不写 Master。

## 6. 当前门禁结论

本次复核完成了证据对账和确定性 Resolver 修复，但没有完成真实人工 disposition。因此：

- Stage 4：仍需处理 `FULL_STAGE4_RELEASE=false` 及两条源冲突，不能自动改为通过；
- Stage 5：仍未 `STAGE5_ACCEPTED`，至少 103 条候选待真实人工审核；
- Stage 6：继续 `DRAFT / NOT_FROZEN`；
- 生产 Master、正式翻译字典、训练 Gold：均未被本次动作写入。

下一步只需由审核者对 Stage 4 包和 Resolver v3（或保留 v2 作为审核基线）填写 disposition；完成后再按 Gate 重放。不要把本文件中的外部建议直接当作人工确认。
