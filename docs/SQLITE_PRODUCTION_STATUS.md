# SQLite Production Source of Truth 状态

更新日期：2026-08-30

## 当前结论

SQLite V2 的事务写入、读路径适配和每日提交接线已经实现，但生产配置仍为：

```yaml
storage:
  mode: EXCEL_PRIMARY
```

因此目前 Excel/CSV 仍是正式主链，SQLite 不会被自动写入或自动接管。

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

受控 V2 baseline migration、三轮 Shadow parity、备份恢复校验、兼容导出确认和副本 Primary
角色演练均已完成。正式生产仍为 `EXCEL_PRIMARY`；正式目标库尚未迁移、尚未 promote。

正式切换前仍需：

1. 选择切换窗口并确认回滚责任人；
2. 在窗口内对正式旧库执行受控 V2 baseline migration，并复核 hash/parity；
3. 确认没有未登记的 legacy direct writer；
4. 单独授权后运行 `db-promote-primary`，再把配置切为 `SQLITE_PRIMARY`；
5. 观察首轮 PRIMARY run 和 `export_sync` 状态。

## 证据

- 测试：`tests/test_database_schema.py`、`tests/test_database_production.py`、
  `tests/test_database_integration.py`；
- 命令：`python -m action_tracker db-status`；当前 runtime 显示旧镜像，产品 8,680 条；
- 旧镜像上的 `db-validate-production` 会安全返回 `DB_V2_SCHEMA_REQUIRED`，不会把 V1 当成生产库。
