# SQLite Cutover Rehearsal 2026-08-30

本次只做副本演练，不修改正式 `runtime/db/action_tracker.db`，不修改
`config/settings.yaml` 的 `storage.mode`。

## 备份

备份目录：`F:\ActionSKUTracker\runtime\backups\cutover_rehearsal_20260830`

包含正式 Master、known/offline State、旧 SQLite、settings.yaml 和 Git HEAD。
备份与恢复副本的 SHA-256 已逐文件比对，5 个文件全部一致。

## 基线迁移演练

目标库：`F:\ActionSKUTracker\runtime\temp\cutover_rehearsal_20260830\action_tracker_v2.db`

- baseline commit：`2026-08-30_BASELINE_2026-08-30_20d2c26b81b1`
- schema：`ACTION_SQLITE_DATA / 2.0.0`
- products：6,046；observations：6,046；commits：1
- SQLite integrity：PASS；foreign keys：PASS；Presence states：PASS
- Excel/SQLite parity：PASS；mismatch：0
- compatibility export acknowledgement：SUCCESS；pending：0

## Primary 角色演练

使用目标库副本 `promote_rehearsal.db` 执行 `promote_database_role`：

- SHADOW → PRIMARY：PROMOTED
- 提升后 integrity/FK/Presence：全部 PASS

正式目标库仍保持旧 V1 镜像，生产角色仍未提升。正式切换前仍需人工确认切换窗口、
回滚责任人和 Primary 下的首轮运行监控。
