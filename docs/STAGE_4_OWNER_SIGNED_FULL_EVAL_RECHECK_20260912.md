# Stage 4 / Stage 5 Owner-Signed Full Evaluation Recheck（2026-09-12）

## 结论

本轮已完成两类证据的合并审计：

1. 业主签字包 `Qwen_Stage4_Stage5_OWNER_SIGNED_20260912.xlsx`：Stage 4 中 13 条 Gold 已确认，3006792、3224748 两条源字段冲突按决定隔离；Stage 5 的 103 条中 91 条具备 Stage 6 apply 条件。
2. 冻结 adapter 对 `stage4_test_only_485.jsonl` 的全量批量评估：485 条、2,910 个字段值全部完成，JSON/schema/非空/一级类目合法率均为 100%，但字段级硬事实门禁仍发现 10 条 P0，另有 1 条 P1。

因此当前**不能**设置 `FULL_STAGE4_RELEASE=true`，也不能签发 `STAGE_4_ACCEPTANCE`。这不是 owner signoff 缺失，而是完整测试覆盖和模型字段忠实度仍未闭合。

> **后续状态更新（2026-09-12）**：项目所有者已明确确认两份 AI Owner Ready 包；485 条 Silver 覆盖与 P0/P1 处置确认已闭环。原始工作簿不改写，确认记录为 `stage4_owner_confirmed_closure_20260912.json`。本报告以下旧的 Silver 覆盖阻断描述由该确认记录取代；10 条模型 P0 仍是发布阻断。

## 评估结果

| 指标 | 结果 |
| --- | ---: |
| 测试 SKU | 485 |
| 评估字段值 | 2,910 |
| JSON 解析率 | 100% |
| 字段 Schema 率 | 100% |
| 字段非空率 | 100% |
| 一级类目合法率 | 100% |
| 数字保留率 | 99.24399% |
| 数字幻觉率 | 0.20619% |
| 西语残留率 | 0.10309% |
| 生产写入 | 0 |

说明：数字表面检查会把重复值合并、`dos/cuatro` 转成中文数字、以及“2 双/2 件”等单位表达差异报成问题；这些已单独列为 surface variation，不直接当作模型 P0。

## 需要修复的 10 条 P0

| SKU | 字段 | 类型 | 问题 |
| --- | --- | --- | --- |
| 3219003 | 品名 | 品牌/数值丢失 | Kate Legwear 与 40 Denier 未保留 |
| 3220894 | 描述 | 跨字段带入 | 将详情中的 6 岁条件新增到描述 |
| 3217699 | 描述 | 数字幻觉 | 新增来源不存在的 1.25L，并跨字段带入 2.5L |
| 3223271 | 描述 | 技术 token 丢失 | H7、P21/5W、W5W、C5W、PY21W 未保留 |
| 3221705 | 描述 | 数值丢失 | Milka 250g 未保留 |
| 3221795 | 描述 | 数值丢失 | 14 个灯泡未保留 |
| 3201998 | 描述 | 数值丢失 | 每卷 64 张未保留 |
| 3213267 | 描述 | 数值丢失 | 27 件未保留 |
| 3009588 | 描述 | 数值丢失 | 最多 5 升未保留 |
| 3209605 | 品名 | 语义错误 | La Isla Living 商品被译成“拖鞋垫”，应为门垫 |

这些问题按当前“字段级硬事实”合同处理，即使同一 SKU 的详情字段仍保留该值，也不能直接放行当前字段输出。

## 1 条 P1 跟进

- `3217469` 描述：来源给出 `12+12/24`，模型保留了 12+12，但没有重复写总数 24。属于派生总数的呈现问题，不与上述 P0 混同。

## 已排除的表面误报

- 同一字段中的重复数字被模型去重。
- 西语数字词（如 `dos/cuatro`）被规范为中文数字。
- 数字与单位词的表达变化（例如“2 双装”）。
- `Marvel Spidey`、`Hello Kitty`、`Disney Stitch`、`La Patrulla Canina`、`Finall Oxi Active Power Gel Multi-Color` 等产品/角色/品牌专名，不算西语残留。

## 当前 Gate

- `OWNER_SIGNED_STAGE4_13`：PASS
- `SOURCE_CONFLICTS_ISOLATED`：PASS
- `NUMERIC_SOURCE_LOSS_CORRECTED`：PASS（旧的 source-loss blocker 已纠正）
- `FULL_EVAL_STRUCTURE`：PASS
- `FULL_EVAL_CATEGORY`：PASS
- `FULL_EVAL_HARD_FACT_ZERO`：FAIL（10 条 P0）
- `STAGE4_RELEASE_GOLD_COVERAGE`：PASS（485/485 已由 Owner Ready 包确认；50 条 remediation candidate 仍未成为 Gold）
- `FULL_STAGE4_RELEASE`：**FALSE**

## 下一步

1. 仅针对上表 10 条 P0 修复 resolver/Guard 或标记为人工覆盖，不重做 Stage 4 全流程，不覆盖旧模型和旧评估产物。
2. 修复后对同一 485 条测试集重跑，要求字段级硬事实问题为 0；P1 可单独记录但应在发布前处理。
3. 从 50 条 remediation candidate 中确认 disjoint Gold；完成最多两轮 targeted retrain，并重跑同一 485 条冻结测试。
4. 通过后再运行 Stage 5 Gate；本报告期间 Master、SQLite、字典和模型产物均未修改。

## 证据文件

- `runtime/training/qwen3_8b/20260911/stage4_stage5_owner_signed_full_closure_recheck_20260912.json`
- `runtime/training/qwen3_8b/20260911/stage4_full_eval_owner_signed_20260912_v2.json`
- `runtime/training/qwen3_8b/20260911/stage4_full_eval_owner_signed_20260912_predictions.jsonl`
- `runtime/training/qwen3_8b/20260911/stage4_full_eval_owner_signed_p0_review_package_20260912.csv`（10 条 P0 + 1 条 P1，供人工逐条确认）
- `runtime/training/qwen3_8b/20260911/stage4_full_eval_owner_signed_p0_triage_20260912.json`（独立源字段核验：10/10 P0 均有直接源字段证据）
- `runtime/training/qwen3_8b/20260911/stage4_final_failure_certification.csv/json`（按最终 Closure 规格记录 source、Gold、split、contract、validator、model 状态及 remediation）
- `docs/STAGE_4_OWNER_SIGNED_RECHECK_20260912.md`
