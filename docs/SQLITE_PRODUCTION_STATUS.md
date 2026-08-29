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

## 真实运行前仍需完成

1. 对当前旧的 `ACTION_SQLITE_MIRROR 1.0.0` 数据库执行一次受控 V2 基线迁移；
2. 使用同一批正式 run 连续完成至少 3 次 Shadow parity（CURRENT、事实、Lifecycle、Presence、
   价格、事件、Review 和派生状态）；
3. 完成备份、恢复、导出失败重试和回滚演练；
4. 确认没有未登记的 legacy direct writer 后，运行 `db-promote-primary`，再把配置切为
   `SQLITE_PRIMARY`。

## 证据

- 测试：`tests/test_database_schema.py`、`tests/test_database_production.py`、
  `tests/test_database_integration.py`；
- 命令：`python -m action_tracker db-status`；当前 runtime 显示旧镜像，产品 8,680 条；
- 旧镜像上的 `db-validate-production` 会安全返回 `DB_V2_SCHEMA_REQUIRED`，不会把 V1 当成生产库。
