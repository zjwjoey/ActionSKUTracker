# SQLite Production Source of Truth 状态

更新日期：2026-08-30

## 当前结论

SQLite V2 的事务写入、读路径适配和每日提交接线已经实现，当前生产配置为：

```yaml
storage:
  mode: SQLITE_PRIMARY
```

SQLite 已成为正式主链；Excel/CSV 保留为由 SQLite head 生成并校验的兼容投影。

## 已实现

- V2 身份：`ACTION_SQLITE_DATA / 2.0.0`，角色 `SHADOW` 或 `PRIMARY`；
- `CommitBundle` 单一提交载荷；
- `BEGIN IMMEDIATE`、外键、完整性检查、FULL synchronous、busy timeout；
- run_id 幂等和 `base_commit_id` 乐观门禁；
- Presence 三态 `PRESENT / ABSENT / UNKNOWN`；
- 价格和事件使用稳定 event key，重复提交不会重复追加；
- `SQLITE_SHADOW`：Excel 提交后镜像 SQLite，SQLite 失败不阻断 Excel 主链；
- `SQLITE_PRIMARY`：先提交 SQLite，再生成兼容 Master/State；兼容文件失败时保留
  `DB_COMMITTED_EXPORT_PENDING`，由 `sync-exports` 重试；
- PRIMARY Read Repository：`load_current_products()`、`load_known_skus()`、离线派生；
- `image_assets` 只保存图片元数据，不保存二进制。

## 当前切换状态

受控 V2 baseline migration、三轮 Shadow parity、备份恢复校验、兼容导出确认、副本 Primary
角色演练和正式切换均已完成。正式数据库当前为 `PRIMARY`，pending export sync 为 0。

正式切换记录：

1. 备份目录：`runtime/backups/formal_cutover_20260830_120733`；
2. 正式基线 commit：`2026-08-30_BASELINE_2026-08-30_20d2c26b81b1`；
3. 正式数据库已执行 `db-promote-primary`；
4. 配置已切换为 `SQLITE_PRIMARY`；
5. 首轮切换后完整性、外键、Presence 和 parity 均 PASS。

代码已提供只读预检命令：

```powershell
python -m action_tracker db-cutover-check
```

预检要求目标库为 V2 `SHADOW`，完整性、外键、Presence 状态、兼容
Master/State 对账和 `export_sync` 均通过；正式提升后该命令会因配置已为
`SQLITE_PRIMARY` 而拒绝，这是防止重复切换的保护。

## 证据

- 测试：`tests/test_database_schema.py`、`tests/test_database_production.py`、
  `tests/test_database_integration.py`；
- 命令：`python -m action_tracker db-status`；当前 runtime 为 V2 PRIMARY，products/lifecycle 各 6,046 条；
- 正式旧 V1 镜像已保存在上述备份目录，可用于回滚，不再作为运行时主库。
