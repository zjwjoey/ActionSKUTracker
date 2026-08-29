# ActionSKUTracker — SQLite Mirror V1 实际验证报告

验证日期：2026-08-30
分支：`feat/sqlite-data-foundation-v1`
输入 Master：`F:\ActionSKUTracker\runtime\master\Action_Master.xlsx`
Mirror：`F:\ActionSKUTracker\runtime\db\action_tracker.db`
Migration ID：待本次最终两项修复后的真实重建回写

## 1. 执行边界

本次只执行 Excel Master → SQLite staging → 校验 → 原子替换 Mirror。没有访问 Action 官网，
没有在本闭环中运行 `daily-run`，没有写入 Excel Master、State、Dictionary，也没有改变 Listing、
Sitemap、Lifecycle、Detail 或 QA 主链。Master 迁移前后 SHA-256 均为：

`a1a5adde31225b093b627cd1e2413c369b5ea9b737ef428b50b13f21d03c5da3`

## 2. 实际源数据与目标数量

| 对象 | Master 实际 | SQLite 实际 | 结果 |
| --- | ---: | ---: | --- |
| 正式商品 `products` | 8,680 | 8,680 | 一致 |
| 中文本地化记录 | 8,680 | 8,680 | 逐字段一致 |
| ZH CURRENT | 5,431 | 5,431 | exact set 一致 |
| ES CURRENT | 5,431 | 5,431 | exact set 一致 |
| `03_PRICE_HISTORY` 有正式 SKU | 14,042 | 14,042 | 逐字段一致 |
| `04_EVENT_HISTORY` 有正式 SKU | 21,000 | 21,000 | 逐字段一致 |
| `05_RUN_LOG` | 18 | 18 | 逐字段一致 |
| `06_REVIEW_QUEUE` | 516 | 516 | 全部保留 |
| `source_records`（10 张 Sheet 非空行） | 70,933 | 70,933 | 行号、Raw、Hash 全一致 |
| observations | 无可证明来源 | 0 | 未伪造每日 observation |

## 3. H1 字段级对账

以下六类业务表均为逐字段 parity，且 mismatch 样本数为 0：

| 表 | 对账记录 | mismatch |
| --- | ---: | ---: |
| products | 8,680 | 0 |
| product_localizations | 8,680 | 0 |
| price_history | 14,042 | 0 |
| events | 21,000 | 0 |
| runs | 18 | 0 |
| reviews | 516 | 0 |

## 4. H2 原始证据对账

`source_records` 覆盖全部 10 张 Sheet 的每一条非空行：

| Sheet | 源行数 | 证据行数 | 结果 |
| --- | ---: | ---: | --- |
| 01_SKU_ZH_CURRENT | 5,431 | 5,431 | PASS |
| 02_SKU_ES_CURRENT | 5,431 | 5,431 | PASS |
| 03_PRICE_HISTORY | 15,012 | 15,012 | PASS |
| 04_EVENT_HISTORY | 22,940 | 22,940 | PASS |
| 05_RUN_LOG | 18 | 18 | PASS |
| 06_REVIEW_QUEUE | 516 | 516 | PASS |
| 07_APRIL_ARCHIVE | 5,993 | 5,993 | PASS |
| 08_LONG_TERM_MASTER | 9,580 | 9,580 | PASS |
| 09_APRIL_MATCH_AUDIT | 5,993 | 5,993 | PASS |
| 10_SOURCE_SCHEMA | 19 | 19 | PASS |

`exact_row_identity=true`、`raw_hash_parity=true`、mismatch=0。`source_records` 与
`migration_source_issues` 分离，任何待匹配或审计行仍有完整 Raw 证据。

## 5. M1/M2/M3 安全 Gate

M1 当前事实边界：`02_SKU_ES_CURRENT` 覆盖当前 SKU 的名称、一级/二级类目、规格、描述、详情、
图片、当前价格和商品链接；`08_LONG_TERM_MASTER` 仍拥有 canonical_id、历史价格、状态、首次/最后
观察日期及来源字段。历史 SKU 不因缺少 02 行而被清空或改写。定向 fixture 覆盖测试已验证该边界。

- Schema family：`ACTION_SQLITE_MIRROR`；version：`1.0.0`；
- 旧库形状被 `db-init` 识别为 `LEGACY_DB_REBUILD_REQUIRED`，不原地升级；V1 新库初始化可重复；
- `PRAGMA foreign_keys=1`；`integrity_check=ok`；`foreign_key_check=[]`；
- ZH CURRENT = ES CURRENT = DB CURRENT，三个集合均为 5,431；ES `Canonical_ID` 精确匹配长期实体；
- 迁移前、迁移后、promotion 前 final Master hash 全相同；
- staging 通过全部校验后才原子替换；promotion 后完整性、foreign key、schema family、schema version、migration_id 校验 PASS；
- 旧 Mirror 在 promotion 失败或 promotion 后校验失败时可由备份恢复；
- 离线 fixture 与全量回归测试通过，未触发官网或生产主链。

## 6. 源数据问题（不掩盖）

本次 `migration_source_issues` 共 15,841 条，属于源数据/审计事实，不是 Mirror 丢失：

| issue_code | 数量 | 含义 |
| --- | ---: | --- |
| `AUDIT_ONLY_SOURCE` | 11,986 | 07/09 两张审计 Sheet，各 5,993 行 |
| `UNMATCHED_CANONICAL` | 3,810 | 900 条长期实体、970 条空 SKU 价格行、1,940 条空 SKU 事件行 |
| `SOURCE_DUPLICATE` | 26 | Review/Source Schema 源重复，原始行仍保留 |
| `SOURCE_SCHEMA_METADATA` | 19 | 10_SOURCE_SCHEMA 元数据行 |

## 7. 报告文件与结论

报告目录：`F:\ActionSKUTracker\runtime\db\reports\20260829T164012Z_89075793\`

- `migration_report.json`
- `validation_report.json`
- `mapping_summary.json`

最终判定：

> **SQLITE MIRROR VALIDATED WITH SOURCE DATA ISSUES**

SQLite Mirror V1 已完成 H1/H2/M1/M2/M3 技术闭环，可作为可复核数据基础层；Excel Master
仍是唯一正式 Source of Truth。尚未授权 SQLite 反写 Master、daily-run 直接写 DB、官网采集
切换到 DB 或字典/生命周期切换到 DB。
