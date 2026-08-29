# 三条主线开发后审查清单

## Export Foundation V1

- [ ] 仅选择 QA PASS + FULL_COMMIT 的唯一正式 run；
- [ ] ES/ZH/CURRENT SKU 集合完全一致；
- [ ] 价格、URL、日期为事实值，导出不访问官网；
- [ ] 无图和带图字段一致；
- [ ] 带图满足 `embedded_count + missing_count = CURRENT SKU count`；
- [ ] manifest 含 run、来源 hash、profile 版本和图片统计。

## SQLite 接管

- [ ] 当前数据库身份为 `ACTION_SQLITE_DATA / 2.0.0`；
- [ ] integrity、foreign key、孤儿记录均通过；
- [ ] run_id 幂等、base commit 门禁和事务 rollback 通过；
- [ ] Shadow 连续 3 次 parity 为 0 mismatch；
- [ ] PRIMARY 读路径不读取 Excel/CSV 做业务决策；
- [ ] 兼容导出失败进入 `DB_COMMITTED_EXPORT_PENDING`，可由 `sync-exports` 重试；
- [ ] 备份/恢复/回滚演练完成后才改 `storage.mode`。

## Image Foundation V1

- [ ] 只同步正式 CURRENT 的 image_url；
- [ ] 下载到 staging，验证后原子 promotion；
- [ ] Manifest 可断点恢复，URL/hash 未变时复用；
- [ ] 失败不改变 Presence、Lifecycle 或 SKU 数量；
- [ ] 250×250 白底 derivative 通过尺寸、模式和背景检查；
- [ ] 错 SKU 图片阻断发布，缺图只留空并保留 SKU。

## 本地证据命令

```powershell
$env:PYTHONPATH = "src"
python -m pytest -q
python -m action_tracker db-status
python -m action_tracker db-validate-production
python -m action_tracker image-status
python -m action_tracker sync-exports
```

当前默认配置仍是 `EXCEL_PRIMARY`；SQLite 生产切换和真实图片同步必须单独记录运行证据。
