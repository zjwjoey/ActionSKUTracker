# 当前仓库与生产状态

历史治理快照截止：2026-10-08；2026-10-10 授权历史中文修复进度见末节。以下旧候选和运行目录描述保留其当时身份，不表示历史修复分支已部署到 daily 主线。

## 已冻结生产

- origin/main：231550eb74d24c927e100bc585aa728bf7ab7f19。
- 标签 production/phase1-3-20261007 指向该 SHA。
- 历史本地完整测试 826、该基线 Ubuntu/Windows CI-safe 各 823。
- Phase 1–3 曾在真实 PRIMARY 验收并发布当次 5,626 SKU；数量不是永久业务规则。
- 正式入口 data-update/production-run，SQLite PRIMARY 权威，Excel/State 为兼容投影。

## 本轮候选

fix/post-production-repository-closure-20261008 从核实 main 建立，在独立 checkout 开发。

1. Preview 独立目录、可信摘要，保护正式/未知三件套。
2. Template 1 共用完整 Release Gate。
3. V2 正式 FACT_COMMIT 验证并复用已提交同日 daily；禁止部分 bundle 独立写 PRIMARY。独立 canary 仍只写临时库。
4. Registry FAILED/PENDING 独立后续状态，Operations DEGRADED，按 run/commit/date 幂等恢复。
5. Operations 传业务日期；daily 计算前冻结 base；V2 共用运行锁及 head 校验。
6. 更新入口、恢复、部署、回滚合同，审批回归加入 CI。

部署状态：候选尚未合并 main，也未替换今日生产代码或重启任务。实际 full/CI-safe 数量、final SHA、exact-head CI 和副本证据位于本轮 runtime/reports/post_production_repository_closure_20261008/；runtime 不提交 Git。

今日实际执行代码位于 F:\ActionSKUTracker_workflow_v2，分支 fix/workflow-v2-phase3-final-closure，HEAD 3d28f0b356ded3fd2b5d24862954b581e0ef60f5。已冻结 main 基线和当前运行 checkout 是不同身份；额外 snapshot-ingest 尚不属于本轮主线候选。该 checkout 本轮保持不变。

## 外部治理与排除范围

main protection 只读查询显示未保护；建议 PR required、Ubuntu/Windows required、禁止 force push/delete，变更须 Owner 授权。PR #5 Selective Workflow V2 runtime backport 已被生产主线取代，建议另行关闭，本轮未合并/关闭。3d28f0b snapshot-ingest 不纳入本轮，须专项审查。

本轮不注册任务、不重新采集、不调用新 Qwen、不修今日数据、不恢复 PRIMARY。参见 [README](../README.md)、[Operations](OPERATIONS_RUNBOOK_V2.md)。

## 2026-10-10 历史中文修复：已验收与待处理

用户授权的独立 checkout 为 `F:\ActionSKUTracker_history_20261009`，分支 `fix/historical-localization-recovery-20261009`。正式数据库仍为 `F:\ActionSKUTracker\runtime\db\action_tracker.db`。历史中文通过既有 Registry / QA / delegated Approval / Immutable Patch / Apply / Master Sync 写入；没有合并或推送 main，也没有发布字典基线或修改 daily 的配置开关。

最新已验收 PRIMARY head：`2026-10-10_historical_existing_spec23_20261010_24e9cb0583f0`。类目第 19、20 批共恢复 1,531 个正确原值的审批及来源绑定；规格第 21、22、23 批分别恢复 38、41、51 个绑定，第 23 批另修正 2 条 Over-ear 特性丢失的规格。各批均完成备份、受保护数据和全库中文差异检查、Master Sync 及实际同批 Resume；重复 Apply 为 0。原值元数据补丁不能计为补译或错误译文修正。

规格独立审查第 21、22、23 组共覆盖 300 组、609 个 SKU 字段：604 个现有中文可保留，2 个已修正并入库；1 个因 `5 pares` 与 `5 unidades` 的包装层级歧义、2 个因 `hojas` 与 `páginas` 的计数层级歧义保留待复核。第 23 批正式 Apply 为 53 个字段（51 个绑定、2 个文本修正），不是缺失字段补译。正确中文不重新翻译。

| 截止此已验收 head 的核实范围 | 数量及解释 |
| --- | --- |
| 历史 SKU / 六字段机械检查 | 4,244 / 25,464；不等同于全量独立语义审查 |
| 非空中文 | 20,470 |
| 既有独立审查证据重新验证 | 3,211；精确原文、目标、来源文件、当前审批和 QA 均核对 |
| 已人工确认类目映射证据重新验证 | 6,629；全部当前有效审批和绑定，单独统计映射政策验证 |
| 上述两类当前有效审核覆盖 | 9,840 个不同字段；其余 10,630 个非空字段仍未被这两类证据覆盖 |
| 缺失中文 | 4,994；可信来源 709，其中 708 与 PRIMARY 精确一致、1 有已记录的不一致 |
| 缺失来源分类 | 缺源 3,460、版本冲突 469、语言复核 355、明确空源 1；不编造中文 |
| 历史实际 Apply 账本 | 16,256 个不同不可变补丁 ID：14,565 个文本改动补丁、1,691 个原值元数据补丁；重复修正单独计补丁，不是唯一字段数 |

最近核心代码为 `cb54da9`：既有字段级 VARIANT 机制保护本字段 Over-ear 的包耳特性，阻断通用头戴式及否定包耳表述；21 项真实来源和边界测试加入 CI_SAFE，完整回归 1,245 项通过。此前 `c84f0e8` 为来源限定的霓虹灯 QA 别名，`0c3cbea` 绑定有限翻译缓存的实际 adapter 配置。本批没有新的模型服务调用。六项真实历史来源 fixture 的 daily 兼容性测试通过，完整在线 daily-run 仍未验证。全六字段独立语义审查及任务整体验收尚未完成。

可追溯证据保存在 `F:\ActionSKUTracker\runtime\reports\historical_localization_20261009`：各批 `acceptance.json`、`existing_spec21_reviewed_all.json`、`existing_spec22_reviewed_all.json`、`existing_spec23_reviewed_all.json`、各批 `owner_queue.json`、`prior_independent_semantic_review_revalidated_after_existing_spec23.json`、`historical_actual_apply_ledger_after_existing_spec23.json`、全字段 `existing_chinese_reaudit_20261010_after_existing_spec23`。这些运行产物不提交 Git。新规则全量扫描另定位到 3013368 描述的同类特性遗漏，已完成本字段独立审查和 QA；`existing_description24_overear_final_apply_scope.json` 中 1 条修正尚未 Apply。下一步先通过正式流程验收此修正，再扩大独立语义审查批次、推进其他字段与可信来源缺失候选；不得把未 Apply 候选计为正式入库。
