# Operations Runbook V2

## 正式入口

    powershell -ExecutionPolicy Bypass -File scripts/run_production_daily.ps1 -Date 2026-10-08 -ProjectRoot F:\ActionSKUTracker
    python -m action_tracker data-update --date 2026-10-08

直接 Python 先设正确 ACTION_TRACKER_PROJECT_ROOT/PYTHONPATH。旧 run_daily.ps1 默认委托官方 wrapper，-DryRun 保留诊断；SQLITE_PRIMARY 禁止 daily-run --no-dry-run。

步骤：PREFLIGHT → BACKUP → COLLECTION → QA → DB_COMMIT → REGISTRY → EXPORT → IMAGE → KNOWLEDGE → AI → AUTO_APPROVAL → REVIEW → REPORT。

EXPORT 是 SQLite 兼容投影同步，不代表 Phase 3 正式中文发布。可选步骤 SKIPPED 不是已执行。Operations 显式传业务日期给 collector 并核验返回；默认 Europe/Madrid，跨午夜/历史日期/Resume 均用冻结 business_date。daily 在读取事实与生命周期前捕获 head，事务拒绝 head 改变。

## 状态与恢复

SUCCESS 表示 Operations 完成或合法跳过，不表示中文全库批准。DEGRADED 表示已提交事实而 Registry/投影等后续失败，保留事实。BLOCKED/FAILED 不允许强制发布。

报告为 runtime/reports/daily/<date>/<operations_run_id>/state.json。核对 delegated run 与 fact commit 后恢复：

    python -m action_tracker production-run --date 2026-10-08 --resume --run-id <operations_run_id>
    python -m action_tracker production-run --date 2026-10-08 --resume --run-id <operations_run_id> --from-step REGISTRY
    python -m action_tracker registry-retry --run-id <collection_run_id> --commit-id <fact_commit_id> --date 2026-10-08
    python -m action_tracker sync-exports --commit-id <current_commit_id>
    python -m action_tracker qa --run-id <collection_run_id>
    python -m action_tracker status

Resume 恢复 COLLECTION allowlist 中日期、来源身份、QA、commit 和 Registry，已成功 COLLECTION/DB_COMMIT 不重复。单独 Registry 恢复后再 Resume 升级总状态。

Registry audit：runtime/reports/registry/<collection_run_id>.json，包含失败原因、retryable、尝试次数/恢复结果。retry 只接受 FAILED/PENDING，核验 commit/date/QA、冻结来源和当前 CURRENT。成功重复请求 ALREADY_READY；来源变化拒绝旧 run，不能恢复旧版本制造 STALE。失败记录缺失时 fail-closed。

兼容投影恢复仅针对当前合法 commit，新 head 的旧 pending 为 SUPERSEDED，不可重放覆盖。

## 锁和故障

Operations、正式 daily、V2 production 共享 state/daily-run.lock；外层持锁后内部 collector _skip_lock=True。独立 registry-retry 自行持同一锁；Resume 使用内部无锁 adapter。

403/429/挑战页进入受控停止；无效观测不推进 MISSING/OFFLINE。Detail 失败不否定有效 Presence。缺售价/来源/批准状态导致正式发布 BLOCKED 时处理真实数据原因，不能降低 Gate。

Backup 使用 SQLite Backup API；恢复须停写、Owner 授权、副本 schema/integrity/foreign keys 与事实对账。普通 Preview 写 exports/preview，正式发布 --research-release。Windows Task Scheduler 与 Codex 提醒不同，本轮未注册任务。参见 [README](../README.md)、[Rollback](WORKFLOW_V2_PRODUCTION_ROLLBACK.md)。
