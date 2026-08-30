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

## 第 1 轮真实 Shadow 结果

隔离 run：`2026-08-30_031120`
SQLite commit：`2026-08-30_2026-08-30_031120_771c8593f372`

- QA：PASS；提交：FULL_COMMIT；SQLite：COMMITTED；兼容导出同步：SUCCESS；
- Sitemap：5,396；Listing：5,395；Listing gap：0%；
- CURRENT Excel / DB：5,396 / 5,396；Known lifecycle Excel / DB：6,046 / 6,046；
- fact mismatch：0；lifecycle mismatch：0；总 parity mismatch：0。

该 run 使用独立临时 Master/State/SQLite，未修改生产文件，计为合格 Shadow **1/3**。

## 第 2 轮真实 Shadow 结果

隔离目录：`F:\ActionSKUTracker\runtime\temp\shadow_run2_20260830`
隔离 run：`2026-08-30_042701`
SQLite commit：`2026-08-30_2026-08-30_042701_b76fc2d3ee76`

- QA：PASS；提交：FULL_COMMIT；SQLite：COMMITTED；兼容导出同步：SUCCESS；
- Sitemap：5,396；Listing：5,395；Listing gap：0%；异常：0；CF/429：0；
- CURRENT Excel / DB：5,396 / 5,396；Known lifecycle Excel / DB：6,046 / 6,046；
- SQLite integrity：PASS；foreign keys：PASS；Presence 状态约束：PASS；
- fact mismatch：0；lifecycle mismatch：0；总 parity mismatch：0；
- 本轮 observations：18,138；commit_batches：3（含 baseline 与前一轮 Shadow）。

该 run 使用第 1 轮结果作为隔离基线，未修改生产文件，计为合格 Shadow **2/3**。

## 第 3 轮真实 Shadow 结果

隔离目录：`F:\ActionSKUTracker\runtime\temp\shadow_run3_20260830`
隔离 run：`2026-08-30_050908`
SQLite commit：`2026-08-30_2026-08-30_050908_90ca37f10fdb`

- QA：PASS；提交：FULL_COMMIT；SQLite：COMMITTED；兼容导出同步：SUCCESS；
- Sitemap：5,396；Listing：5,382；Listing gap：0.3%；异常：0；CF/429：0；
- CURRENT Excel / DB：5,396 / 5,396；Known lifecycle Excel / DB：6,046 / 6,046；
- SQLite integrity：PASS；foreign keys：PASS；Presence 状态约束：PASS；
- fact mismatch：0；lifecycle mismatch：0；总 parity mismatch：0；
- 本轮 observations：24,184；commit_batches：4（含 baseline 与前两轮 Shadow）。

该 run 使用第 2 轮结果作为隔离基线，未修改生产文件，计为合格 Shadow **3/3**。

## 后续生产切换门禁

三轮真实 Shadow 均已通过，下一阶段不再是继续 Shadow，而是受控的生产切换评审：

1. 使用同一份正式 daily observation 同时完成 Excel 提交和 SQLite 写入；
2. 对该 run 记录 `commit_id`、`base_commit_id`、QA、来源 hash 和导出确认；
3. 对账 CURRENT、生命周期、事实字段和事件，要求 0 mismatch；
4. 先完成备份、恢复/回滚演练和目标库迁移复核；
5. 经单独授权后，才可执行 `db-promote-primary` 并修改 `storage.mode`。

当前 `config/settings.yaml` 仍保持 `storage.mode: EXCEL_PRIMARY`，生产数据库也仍为旧 V1 镜像。
