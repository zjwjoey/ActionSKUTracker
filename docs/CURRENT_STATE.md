# 当前仓库与生产状态

快照截止：2026-10-08。本文件记录已核实生产基线及本轮分支合同，不追逐文档自身提交 SHA。

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

## 外部治理与排除范围

main protection 只读查询显示未保护；建议 PR required、Ubuntu/Windows required、禁止 force push/delete，变更须 Owner 授权。PR #5 Selective Workflow V2 runtime backport 已被生产主线取代，建议另行关闭，本轮未合并/关闭。3d28f0b snapshot-ingest 不纳入本轮，须专项审查。

本轮不注册任务、不重新采集、不调用新 Qwen、不修今日数据、不恢复 PRIMARY。参见 [README](../README.md)、[Operations](OPERATIONS_RUNBOOK_V2.md)。
