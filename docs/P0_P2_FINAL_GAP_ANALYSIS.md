# ActionSKUTracker P0–P2 最终 Gap Analysis

审查日期：2026-08-30  
审查分支：`feat/export-foundation-v1`  
本地 HEAD：`503a2950ce3633ed491653b03c6d52c01859af5f`  
远端状态：本机 `git fetch origin` 因代理连接 `127.0.0.1:443` 失败，未把不可验证的远端信息当作结论。

## 审查方法

本报告只根据四类证据判定：

1. 当前代码路径和 CLI；
2. 当前测试套件（`295 passed`）；
3. `runtime/db/action_tracker.db`、`runtime/snapshots`、`runtime/exports`、`runtime/images` 的真实状态；
4. 实际运行正式导出命令并重新打开生成的工作簿。

旧文档中标记为 RELEASED/PRIMARY/PASS 的内容，如果没有上述证据，不直接视为完成。

## P0 — Export Foundation V1

| 状态 | Feature | Current Code | Current Test | Current Production State | Gap | Severity | Required Action |
|---|---|---|---|---|---|---|---|
| IMPLEMENTED / VALIDATED | ES/ZH 无图正式导出 | `exporting/service.py`、`profiles.py`、`excel_writer.py`、`cli.py` | `tests/test_exporting.py`；全套 295 passed | 用 `2026-08-29_184646` 正式 run 实际生成 ES/ZH，均 5396 SKU | 无功能缺口；直接按日期不指定 run_id 时若同日有多个正式 run 会按契约拒绝歧义 | LOW（契约行为） | 正式发布命令显式传 `--run-id`，不改算法 |
| IMPLEMENTED / VALIDATED | ES/ZH SKU、价格、链接 parity | `validate_zh_rows_against_source()`、`validate_output_rows()` | Export parity fixture tests | 实际生成的 ES/ZH 工作簿均 5396 行，使用同一正式快照 | 无 | — | 保持现状 |
| IMPLEMENTED / VALIDATED | 无网络、只读导出 | `resolve_formal_source()`、导出服务无 HTTP 调用 | 只读 hash/导出 fixture tests | 8/30 导出过程中未访问官网；源数据库和 Master 未被写入 | 无 | — | 保持现状 |
| IMPLEMENTED / VALIDATED | History Presence 三态及审计 | `exporting/history.py`、`history_export.py` | `tests/test_history_export.py` | 独立历史导出实际通过：14606 SKU、17 日期、包含“历史来源审计” | 无 | — | 保持现状 |
| IMPLEMENTED / VALIDATED | Template1 三表及单一 History 服务 | `template1_service.py`、`template1.py` | `tests/test_template1.py` | 8/29 指定正式 run 实际生成无图/带图 Template1，ES=ZH=当前 5396 | 无 | — | 保持现状 |
| IMPLEMENTED / VALIDATED | manifest 来源、SKU、图片及历史统计 | `service.py`、`template1_service.py` | Export/Template fixture tests | 六个实际导出 manifest 均生成；ES/ZH 单表 manifest 已记录 17 个历史来源 | 无 | — | 保持现状 |
| IMPLEMENTED / VALIDATED | 当前日期正式导出 | `resolve_formal_source()` + 2026-08-30 正式 run | Export/Template tests | 2026-08-30 正式 run `2026-08-30_074743` 已 FULL_COMMIT；指定 run_id 的 ES/ZH/Template1 导出通过 | 无；不指定 run_id 时同日多源仍按契约拒绝歧义 | LOW（契约行为） | 正式发布命令显式传 `--run-id` |

### P0 真实输出证据

2026-08-30 正式 run `2026-08-30_074743` 已完成 `FULL_COMMIT`，并已生成同日 ES/ZH 无图、ES/ZH 带图及 Template1 无图/带图输出；各清单均为 5396 SKU。

以下均使用 `2026-08-29_184646`（同日两个正式 run 中显式选择最新一轮），输出位于 `runtime/exports/`：

- `20260829Action商品全量_西班牙语版_不带图.xlsx`：5396 SKU；
- `20260829Action商品全量_中文版_不带图.xlsx`：5396 SKU；
- `20260829Action商品全量_三表版_不带图.xlsx`：ES/ZH 5396，History 14606；
- 对应带图版本均成功生成，当前本地没有可用图片，因此 `embedded=0 / missing=5396`，未删除任何 SKU；
- `20260830_Action商品上下架明细.xlsx`：14606 SKU、17 个历史日期、三态校验通过。

## P1 — SQLite Production Source of Truth

| 状态 | Feature | Current Code | Current Test | Current Production State | Gap | Severity | Required Action |
|---|---|---|---|---|---|---|---|
| IMPLEMENTED / VALIDATED | DB identity / PRIMARY | `database/schema.py`、`production.py` | production/schema tests | `ACTION_SQLITE_DATA / 2.0.0 / PRIMARY` | 无 | — | 保持现状 |
| IMPLEMENTED / VALIDATED | Transaction、BEGIN IMMEDIATE、rollback、base_commit_id、幂等 | `database/production.py` | fault injection、idempotency tests | 当前 DB 1 个 baseline commit，完整性和 FK PASS | 无 | — | 保持现状 |
| IMPLEMENTED / VALIDATED | Lifecycle/Presence SoT | `repository.py`、`daily.py` | parity/lifecycle tests | CURRENT 5396；orphan 0；UNKNOWN/PRESENCE 校验通过 | 无 | — | 保持现状 |
| IMPLEMENTED / VALIDATED | Master/known/offline 兼容投影及 sync-exports | `database/integration.py` | integration/fault tests | `pending_export_sync=0`；db-status、db-validate-production PASS | 无 | — | 保持现状 |
| IMPLEMENTED / VALIDATED | Backup/restore/integrity | `production.py`、`connection.py` | backup/restore tests | `runtime/backups/formal_cutover_20260830_final.db` 存在；隔离恢复演练通过 | 无 | — | 保持现状 |
| IMPLEMENTED / VALIDATED | legacy writer guard | `baseline.py`、`excel/writer.py` | `test_master_structure.py`、production tests | PRIMARY 下旧写入路径被阻断 | 无 | — | 保持现状 |
| IMPLEMENTED / VALIDATED | image_assets metadata table | `schema.py`、`persist_image_manifest()` | integration image metadata test | 表存在；5396 条 Manifest 元数据已镜像，二进制不入 DB | 无 | — | 保持现状 |

## P2 — Image Foundation V1 + With-Images Export

| 状态 | Feature | Current Code | Current Test | Current Production State | Gap | Severity | Required Action |
|---|---|---|---|---|---|---|---|
| IMPLEMENTED / VALIDATED | SKU 资产路径、Manifest、状态机 | `images/assets.py` | `tests/test_image_foundation.py` | 真实 Manifest 5396 条，全部 `AVAILABLE` | 无 | — | 保持现状 |
| IMPLEMENTED / VALIDATED | 下载校验、解码、PNG master、staging 原子 promotion | `images/sync.py` | normalize/bad-content tests | 5396/5396 HTTP 图片下载成功，解码和 PNG Master 全部通过 | 无 | — | 保持现状 |
| IMPLEMENTED / VALIDATED | 250×250 白底 derivative | `images/derivatives.py` | derivative test | `runtime/images/derivatives/excel_250` 已生成 5396 张，抽样 250×250/RGB/白底通过 | 无 | — | 保持现状 |
| IMPLEMENTED / VALIDATED | ES/ZH/Template1 带图导出 | `exporting/service.py`、`template1_service.py` | image export tests | 8/29 实际带图导出成功；当前 0 embedded、5396 missing，SKU parity 保持 | 带图输出依赖本地资产，不能在 Export 阶段联网补图 | LOW（按契约） | 资产可用后重复导出；缺图保持空白并进 manifest |
| IMPLEMENTED / VALIDATED | 图片状态与带图导出的绑定 | `excel_writer.py`、`service.py`、`template1.py` | 图片导出回归测试 | 配置的 Manifest 存在时只嵌入 `AVAILABLE + PASS` SKU；当前 Manifest 不存在，5396 条均留空 | 无代码缺口；真实同步仍是运行验收事项 | — | 运行 image-sync 后复核 coverage |

## 结论与执行边界

当前代码、fixture 测试和 2026-08-30 真实运行验收均已完成，未发现 HIGH/MEDIUM 代码缺口。2026-08-30 正式 run 已建立真实 observation_date/run_id，P2 图片同步、Manifest、衍生图和带图导出均已通过。

仍需注意的只是发布操作约束：同日存在多个正式 run 时必须显式传 `--run-id`；Git 远端 fetch 本轮因本机代理不可用未重新验证，且本轮代码修改尚未提交。

按任务要求，LOW/SUGGESTION 不改，采集、Lifecycle、AccessController 和 SQLite schema 既有语义不重写。
