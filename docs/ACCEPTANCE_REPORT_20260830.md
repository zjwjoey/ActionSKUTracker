# ActionSKUTracker 三模块验收报告

验收日期：2026-08-30  
验收范围：Export Foundation V1、SQLite Production Source of Truth 核心实现、Image Foundation V1 与带图 Export。  
原则：只读正式 Master/State，SQLite 使用独立临时验收库；不改 `storage.mode`，不切换生产主链。

## 结论

| 模块 | 代码/接口验收 | 真实运行验收 | 结论 |
| --- | --- | --- | --- |
| Export Foundation V1 | PASS | PASS | 可用 |
| SQLite V2 接管核心 | PASS | 临时基线 PASS；生产切换未执行 | 条件通过，未获准切换 Primary |
| Image Foundation V1 | PASS | 缺图容错 PASS；真实图片覆盖未执行 | 条件通过，图片资产任务待执行 |

本次**不能给出“SQLite 已正式接管”或“带图商品表已有真实图片覆盖”**的结论：生产 runtime 仍为
`ACTION_SQLITE_MIRROR / 1.0.0`，图片 Manifest 当前为 0 条。这是当前真实状态，不是测试失败。

## 1. 版本与回归

- 验收分支：`feat/export-foundation-v1`；
- 最新相关提交：`91ce719`；
- 完整回归：`262 passed in 17.97s`；
- `git diff --check` 无格式错误；
- 工作区存在字典、报告、assets/outputs 等既有未提交改动，本次验收未修改、未提交这些文件。

## 2. SQLite V2 临时基线验收

临时数据库：`runtime/temp/acceptance_sqlite_v2_20260830T002840Z.db`。

| 检查 | 结果 |
| --- | --- |
| `products` | 6,046 |
| `observations` | 6,046 |
| `commit_batches` | 1 |
| SQLite integrity | PASS |
| foreign keys | PASS |
| Presence 状态约束 | PASS |
| CURRENT Excel / DB | 5,396 / 5,396 |
| Known lifecycle Excel / DB | 6,046 / 6,046 |
| parity mismatch | 0 |

当前生产 `runtime/db/action_tracker.db` 仍是旧 `ACTION_SQLITE_MIRROR / 1.0.0`，因此
`db-validate-production` 对它拒绝为 V2 是预期安全行为。

随后第 1 轮真实隔离 Shadow（`2026-08-30_031120`）已通过：QA/FULL_COMMIT、SQLite COMMITTED、
兼容导出同步 SUCCESS，CURRENT 与 lifecycle parity 均为 0 mismatch，计为 Shadow 1/3。

第 2 轮真实隔离 Shadow（`2026-08-30_042701`）也已通过：QA/FULL_COMMIT、SQLite COMMITTED、
兼容导出同步 SUCCESS，CURRENT 与 lifecycle parity 均为 0 mismatch，计为 Shadow 2/3。
两轮均使用独立临时 Master/State/SQLite，未修改生产文件。

第 3 轮真实隔离 Shadow（`2026-08-30_050908`）已通过：QA/FULL_COMMIT、SQLite COMMITTED、
兼容导出同步 SUCCESS，CURRENT 与 lifecycle parity 均为 0 mismatch；Sitemap 5,396、Listing 5,382、
gap 0.3%，无 CF/429/异常。三轮均使用隔离 Master/State/SQLite，累计 Shadow 3/3。

随后完成切换演练：正式 Master/State/旧 SQLite/config/Git HEAD 已备份并做恢复哈希校验；
隔离目标库完成 V2 baseline migration、integrity/FK/Presence/parity 检查（全部 PASS，mismatch 0），
兼容导出确认 SUCCESS；Primary 角色提升也仅在目标库副本上演练通过。正式生产仍未 promote。

## 3. 正式 Excel 输出验收

使用正式 run `2026-08-29_184646`（QA PASS、FULL_COMMIT）生成并核验：

- ES 无图、ZH 无图、ES 带图、ZH 带图：每份 5,396 条 SKU，表范围 `A1:N5397`；
- Template 1 无图、带图：历史表 14,672 条，ES/ZH 当日表各 5,396 条；
- 所有工作表冻结首行 `A2`，并具有与数据范围一致的筛选；
- ES/ZH 单表导出的 SKU set hash 完全一致；Template 1 manifest 明确记录 ES/ZH SKU 集合相等；
- 价格、链接等事实字段来自同一正式来源；导出过程未访问官网。

## 4. 图片验收

| 输出 | 嵌入图片 | 缺图 | SKU 是否保留 |
| --- | ---: | ---: | --- |
| ES 带图 | 0 | 5,396 | 是 |
| ZH 带图 | 0 | 5,396 | 是 |
| Template 1 带图（仅中文表允许嵌图） | 0 | 5,396 | 是 |

没有本地图片资产时，0 张嵌图是预期结果；manifest 的 `embedded + missing = 5,396`，未出现图片缺失导致 SKU 被删除的情况。

代码 fixture 同时覆盖了真实有图路径：中文 Template 1 工作表嵌图，历史表和今日西语表不嵌图。

## 5. 视觉验收说明

本机未检测到 LibreOffice/soffice 渲染器，因此未进行 PDF/PNG 截图渲染。已做 OOXML 结构校验（工作表、数据范围、冻结、筛选、媒体部件、manifest）和 openpyxl 回读校验；正式图片下载后应补一次带真实图片的视觉抽检。

## 6. 生产门禁（未执行）

以下项目需要单独批准，不能随本次验收自动完成：

1. 备份 Master、State 和旧 runtime 数据库；
2. 对正式目标库执行 `db-migrate-baseline`；
3. 三轮 `SQLITE_SHADOW` 已完成且每轮 parity 均为 0 mismatch；
4. 目标库迁移、备份/恢复和 Primary 角色演练已在副本完成；正式切换仍需明确窗口、责任人和回滚确认，才可显式 `db-promote-primary` 与修改 `storage.mode`；
5. 执行正式图片的切片、全量同步和性能基线，再补视觉抽检。

## 7. 正式切换补充记录（2026-08-30）

经用户授权，已完成正式 SQLite 切换。旧 V1 镜像先备份至
`runtime/backups/formal_cutover_20260830_120733`，随后从正式 Master/State 构建 V2
基线。由于旧 V1 的 `products`/`runs` 表定义与 V2 不兼容，迁移采用临时 V2 库成功后
原子替换的方式完成。

- 基线 commit：`2026-08-30_BASELINE_2026-08-30_20d2c26b81b1`；
- products/lifecycle：6,046 / 6,046；CURRENT：5,396；
- integrity、foreign keys、Presence：PASS；parity mismatch：0；
- export_sync：SUCCESS，pending：0；
- 正式数据库角色：`PRIMARY`；配置：`storage.mode=SQLITE_PRIMARY`；
- 切换后全量回归：`283 passed`。

本节记录的是原第 6 节之后的正式切换结果；图片全量同步和 Knowledge Production 正式
Apply 仍未开启。
