# SQLite PRIMARY Cutover Final Acceptance Audit

**审计日期：** 2026-08-30  
**仓库：** `F:\ActionSKUTracker`  
**分支：** `fix/p0-p1-final-closure`（Stage A 收口分支）
**审计范围：** SQLite PRIMARY 正式切换、数据完整性、恢复、兼容导出和旧写入器封锁。未启用图片、翻译、Scoped Dictionary 或 Knowledge Production。

## 结论摘要

本次审计发现并关闭了三类生产风险：初始基线没有导入 Master 的价格/事件/审核/运行历史；长期官方身份未全部进入 V2 导致历史行可能成为孤儿；`sync-exports` 只确认文件存在而没有从 DB HEAD 重建。另发现旧的 baseline/Excel 写入入口在 PRIMARY 下缺少明确拒绝，已加门禁；数据库备份改为 SQLite Backup API。

最终生产库已在隔离副本重建、校验后原子替换，兼容文件重新从 PRIMARY HEAD 生成并同步成功。

## 生产身份与状态

| 项目 | 结果 |
|---|---|
| Git HEAD | `aeb1656c5b3dbb6c4afef37a0175f6fa42a64c6b` |
| CI | GitHub Actions run `33293801264` PASS |
| `storage.mode` | `SQLITE_PRIMARY` |
| schema family | `ACTION_SQLITE_DATA` |
| schema version | `2.0.0` |
| database role | `PRIMARY` |
| latest commit | `2026-08-30_BASELINE_2026-08-30_087273d70325` |
| products | 8,680 个官方长期 SKU |
| lifecycle_state | 6,046 条生命周期身份 |
| CURRENT | 5,396 |
| offline 派生 | 599 |
| export_sync | `SUCCESS`，pending=0 |

## Parity 与数据完整性

- CURRENT：Excel / SQLite 均为 5,396，SKU 集合一致，字段 mismatch=0。
- Lifecycle：Excel known 与 SQLite lifecycle 均为 6,046，字段 mismatch=0。
- Presence：5,396 条正式基线观测，状态均为 `PRESENT/ABSENT/UNKNOWN` 合法值；长期历史身份没有被伪造为 PRESENT 观测。
- 价格历史：Master 中有正式 SKU 的 14,043 行全部进入 SQLite。
- 事件历史：Master 中有正式 SKU 的 21,004 行全部进入 SQLite；另有 1,940 行是四月归档实体、没有正式 SKU，未伪造匹配，已写入 `migration_source_issues` 作为可追溯证据。
- 审核队列：517 / 517。
- 运行日志：Master 19 条历史运行日志 + 1 条基线迁移运行记录；统计原始行保存在 `run_evidence`。
- orphan 检查：observations、price_history、event_history 均为 0。
- `PRAGMA integrity_check`：PASS；`PRAGMA foreign_key_check`：PASS。

详细机器结果：`runtime/temp/cutover_acceptance_20260830/parity_report.json`。

## SQLite 连接与事务

生产连接实测：`foreign_keys=1`、`journal_mode=wal`、`synchronous=2 (FULL)`、`busy_timeout=10000`。采集与计算在事务外完成，CommitBundle 校验后才执行短事务；事务异常会整体 rollback。重复 `run_id`、`base_commit_id` 不一致和 bundle 幂等门禁均保持有效。

故障注入覆盖 run、products、localization、observation、lifecycle、price、event、review、transaction validation 九个阶段；每次注入后所有业务表行数均回到事务前状态。相关回归测试在 `tests/test_database_production.py`。

## 兼容导出与恢复

`sync-exports` 现在执行：读取当前 DB HEAD → 暂存并验证 Master/known/offline → 原子替换 → 写入 `export_sync=SUCCESS`。中途异常会恢复 Master、known 和 offline 的旧内容；正式库已在隔离副本完成一次真实恢复演练，恢复后的 DB identity、完整性、FK、历史孤儿检查全部通过。

SQLite Backup API 已加入 `backup_database()`，并实际生成：

`runtime/backups/formal_cutover_20260830_final.db`

原正式切换回滚资料仍保留在：

`runtime/backups/formal_cutover_20260830_120733/`

该目录包含切换前 Master、旧版 V1 SQLite、known/offline、settings、Git HEAD 和 sha256 清单；它是旧 V1 回滚基线，不等同于当前 V2 PRIMARY 数据库。

## Writer 审计与功能开关

- PRIMARY 正式写入顺序为：Collection → Presence → Lifecycle → QA → Snapshot → CommitBundle → SQLite transaction → COMMIT → 兼容导出。
- `baseline.build_baseline()`、旧 Master writer、详情回写和 Dictionary Apply 通过旧 writer 入口时，在 `SQLITE_PRIMARY` 下拒绝；兼容导出使用显式 projection 标记。
- `known_skus.csv` 仅为 SQLite lifecycle 的兼容视图；`offline_skus.csv` 由 lifecycle 派生。
- `images.enabled=false`、`dictionary_apply.production_enabled=false`、`knowledge.production_apply_enabled=false`、`scoped_dictionary.enabled=false`、`translation.ai_enabled=false`、`translation.auto_approval_enabled=false`。
- PRIMARY 下再次执行 `db-cutover-check` 会安全拒绝重复切换（配置已是 `SQLITE_PRIMARY`），不会改变数据库。

## Findings

### HIGH（已关闭）

- **H-01：** 初始 PRIMARY 基线缺少 Master 的价格/事件/审核/运行历史，无法完整重建长期 Master。修复：基线迁移导入官方历史、长期官方身份和运行证据；正式库已重建并对账。
- **H-02：** 历史记录涉及 6,046 之外的官方 SKU 时会产生孤儿。修复：将长期总表 8,680 个官方 SKU 纳入 products，未匹配归档事件留在 migration_source_issues。
- **H-03：** PRIMARY 下旧状态/Excel writer 可被独立调用。修复：baseline 与 Master stage 入口硬门禁，并覆盖回归测试。

### MEDIUM（已关闭）

- **M-01：** `sync-exports` 原先只确认文件，不能从 DB HEAD 重建。修复：实现 staged rebuild、验证、恢复和 `export_sync` 成功记录。
- **M-02：** 原切换流程缺少 WAL-safe Backup API。修复：加入并实测 SQLite Backup API，保留正式备份和恢复演练。
- **M-03：** 事务阶段故障注入覆盖不足。修复：九阶段回滚测试全部通过。

### LOW / 已接受

- 旧 V1 回滚备份的历史表结构与 V2 不同；其用途是回滚取证，不作为当前 PRIMARY 读取源。
- 1,940 条无正式 SKU 的四月归档事件不能安全自动匹配；已保留源证据，不把猜测写入正式事件。
- Master/CSV 兼容文件是派生视图，生成时文件 hash 会因合法重建而变化；SQLite 才是正式 source of truth。

## 测试与提交

- targeted database/lifecycle/export tests：84 passed。
- 原切换验收 full regression：295 passed，0 failed，0 error；Stage A 分支当前完整回归：293 passed，0 failed，0 error。
- 审计相关修改对应的 GitHub Actions run `33293801264` 已完成且 success；未执行 main merge。

## Final Verdict

在本地与 GitHub CI 验收条件下：HIGH=0、MEDIUM=0，完整性、FK、CURRENT/Lifecycle/Presence parity、历史导入、恢复、事务回滚、导出恢复和旧 writer guard 均 PASS。结论冻结为 **CUTOVER_ACCEPTED**。

下一步：等待用户确认是否进入 main merge。
