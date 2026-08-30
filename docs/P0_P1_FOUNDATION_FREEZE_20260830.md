# P0/P1 Foundation Freeze

冻结日期：2026-08-30  
仓库：`F:\\ActionSKUTracker`  
Stage A 分支：`fix/p0-p1-final-closure`  
基线：`origin/main=dd0fa617035dc326658779efb0e5c69ac2ae5846`  
当前 HEAD：`50a83c39fa22f9068acaaba72df474c8108d64cd`

## 冻结范围

P0 Export Foundation 与 P1 SQLite Production Source of Truth 已进入收口审查。后续变更只接受 BUG、SECURITY 或 DATA INTEGRITY 修复，不在本分支重写采集、Presence、生命周期、Cloudflare 控制或 UI。

## P0 输出

- ES/ZH 全量不带图导出：由通过 QA 的 SQLite CURRENT 生成。
- Template 1、历史导出与 manifest：沿用已验证的独立投影。
- 导出为只读投影，不回写 Master、State 或 Dictionary。
- SKU 唯一性、空 SKU、价格类型、URL、日期和集合一致性属于导出门禁。

## P1 生产主链

- SQLite schema：V2，生产角色：`PRIMARY`，配置：`storage.mode=SQLITE_PRIMARY`。
- Product Structured Truth：当前 runtime 约 8,680 条；Lifecycle：约 6,046 条。
- Excel/CSV/Master 是由 SQLite HEAD 生成的兼容视图，不是生产写入源。
- 生产写入具备事务、备份、完整性/FK 校验、parity 校验和本地化覆盖率防回退门禁。
- 已知本地化回退只能通过审计后的 `db-repair-localization-regression` 定向修复。

## 已知关闭项与保留项

- `bcb8709` 本地化回退防护 hotfix 已带入 Stage A，形成 `50a83c3`。
- 旧备份和回滚证据保留，不删除。
- 图片真实切片、全量同步、性能基线属于后续 P2，不计入本次 P0/P1 完成。
- P3–P6 Knowledge/Dictionary Production gates 保持关闭。

## 验收要求

Stage A 必须通过目标测试、完整回归、只读 `db-status`/`db-validate-production`，并在 CI 成功后才允许进入 P2。任何失败均不得宣称正式发布。
