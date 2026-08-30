# SQLite Shadow 阶段准备记录

日期：2026-08-30  
阶段：Shadow 准备 / 基线验收（不是正式 Primary 切换）

## 已完成

备份目录：

`F:\ActionSKUTracker\runtime\backups\shadow_prep_20260830_0035`

已备份：

- `Action_Master.xlsx`
- `action_tracker.db`（旧 V1 镜像）
- `known_skus.csv`
- `offline_skus.csv`

候选数据库：

`F:\ActionSKUTracker\runtime\temp\shadow_candidate_20260830_0035.db`

候选数据库由当前 Master/State 建立，没有覆盖生产数据库。

## 候选库验收结果

| 检查 | 结果 |
| --- | --- |
| schema | V2 |
| products | 6,046 |
| observations | 6,046 |
| commit_batches | 1（baseline） |
| SQLite integrity | PASS |
| foreign keys | PASS |
| Presence 状态约束 | PASS |
| CURRENT Excel / DB | 5,396 / 5,396 |
| Known lifecycle Excel / DB | 6,046 / 6,046 |
| parity mismatch | 0 |

## 尚未计入的内容

上述只是“候选库基线验收”，不能代替真实 Shadow run。正式 Shadow 还需要：

1. 使用同一份正式 daily observation 同时完成 Excel 提交和 SQLite 写入；
2. 对该 run 记录 `commit_id`、`base_commit_id`、QA、来源 hash 和导出确认；
3. 对账 CURRENT、生命周期、事实字段和事件，要求 0 mismatch；
4. 连续完成 3 轮真实 Shadow 后，才进入备份/回滚复核和 Primary 切换评审。

当前 `config/settings.yaml` 仍保持 `storage.mode: EXCEL_PRIMARY`，生产数据库也仍为旧 V1 镜像。
