# SQLite PRIMARY 生产状态

快照截止 2026-10-08；冻结 main、标签与候选部署状态见 [CURRENT_STATE](CURRENT_STATE.md)。

PRIMARY 是事实、Presence、Lifecycle、Price/Event、来源版本、中文/provenance 的唯一正式来源。实际验证 schema family ACTION_SQLITE_DATA、version 2.0.0、role PRIMARY，不因文件名假定身份。

daily 先捕获 head/读取事实与生命周期，将有效 QA、Collection Quality metrics hash 放入完整 CommitBundle。事务核验预期 base，包括明确空初始 head；head 改变拒绝旧计算。同日状态和事件遵循既有幂等规则，质量 FAIL/缺证据不能入库。

V2 production FACT_COMMIT 只验证并复用同日 daily，不写部分 PRESENT-only bundle；独立 writer 仅用于临时 canary。两条正式编排共享 state/daily-run.lock。候选合并/部署前，生产仍执行冻结版。

Registry 和 compatibility export 是 Fact Commit 后独立状态。Registry FAILED/PENDING 保留事实、Operations DEGRADED，按 run/commit/date 精确重试。兼容投影 Pending 仅恢复合法当前 commit，旧 head SUPERSEDED。

Master/known_skus/offline_skus/Excel 为受约束投影，不用旧表格恢复事实，不直接改 hash。Export 不写 PRIMARY；Preview/正式目录隔离，数据缺口阻断发布，不回滚有效事实。

Backup 使用 SQLite Backup API；恢复须 Owner 授权、停写、副本兼容性/integrity/foreign keys 验证。本轮没有 schema 迁移。

    python -m action_tracker db-status
    python -m action_tracker db-validate-production
    python -m action_tracker status

参见 [README](../README.md)、[Operations](OPERATIONS_RUNBOOK_V2.md)、[Rollback](WORKFLOW_V2_PRODUCTION_ROLLBACK.md)。
