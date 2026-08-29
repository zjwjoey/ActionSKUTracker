# ActionSKUTracker — SQLite Mirror V1 实际验证报告

验证日期：2026-08-29
分支：`feat/sqlite-data-foundation-v1`
输入 Master：`F:\ActionSKUTracker\runtime\master\Action_Master.xlsx`
Mirror：`F:\ActionSKUTracker\runtime\db\action_tracker.db`
Migration ID：`20260829T132648Z_b71f1151`

## 1. 执行边界

本次仅执行 Excel Master → SQLite staging → 校验 → Mirror 替换。没有访问 Action 官网，没有运行 `daily-run`，没有写入 Excel Master、State、Dictionary，也没有改变 Listing、Sitemap、Lifecycle 或 QA 主链。Master 在迁移前后均为同一 SHA-256：

`a1a5adde31225b093b627cd1e2413c369b5ea9b737ef428b50b13f21d03c5da3`

## 2. 实际源数据与目标数量

| 对象 | Master 实际 | SQLite 实际 | 结果 |
| --- | ---: | ---: | --- |
| 正式商品 `products` | 8,680 | 8,680 | 一致 |
| 中文/本地化记录 | — | 8,680 | 迁移完成 |
| ZH CURRENT | 5,431 | 5,431 | exact set 一致 |
| ES CURRENT | 5,431 | 5,431 | exact set 一致 |
| `03_PRICE_HISTORY` 有正式 SKU | 14,042 | 14,042 | 一致 |
| `04_EVENT_HISTORY` 有正式 SKU | 21,000 | 21,000 | 一致 |
| `05_RUN_LOG` | 18 | 18 | 一致 |
| `06_REVIEW_QUEUE` | 516 | 516 | 全部保留 |
| observations | — | 0 | 本次没有伪造每日 observation |

CURRENT 校验为：

```text
ZH_CURRENT SKU set = ES_CURRENT SKU set = DB current SKU set
```

## 3. SQLite 完整性 Gate

- Schema version：`1.0.0`
- `PRAGMA foreign_keys`：`1`
- `PRAGMA integrity_check`：`ok`
- `PRAGMA foreign_key_check`：空
- products 正式 SKU 集合：通过
- Master SHA-256 记录与前后哈希：通过
- staging 校验通过后才替换 Mirror；旧 Mirror 若存在会先备份

## 4. 源数据问题（已保留，不掩盖）

技术一致性通过，但源文件本身有待匹配/审计数据，因此最终判定不是“无问题 PASS”，而是：

> **SQLITE MIRROR VALIDATED WITH SOURCE DATA ISSUES**

本次 `migration_source_issues` 共 15,836 条：

| issue_code | 数量 | 含义 |
| --- | ---: | --- |
| `UNMATCHED_CANONICAL` | 3,810 | 900 个无正式 SKU 的长期实体、970 条空 SKU 价格行、1,940 条空 SKU 事件行；没有伪造 SKU |
| `AUDIT_ONLY_SOURCE` | 11,986 | `07_APRIL_ARCHIVE` 与 `09_APRIL_MATCH_AUDIT` 各 5,993 行，仅作为审计证据 |
| `SOURCE_DUPLICATE` | 26 | Review Queue 25 条受影响重复行 + Source Schema 1 条重复行，原始业务行仍保留 |
| `SOURCE_SCHEMA_METADATA` | 19 | `10_SOURCE_SCHEMA` 的来源元数据记录 |

其中 900 条四月待匹配实体没有写入 `products`；它们仅保留为迁移审计证据。

## 5. 报告文件

本次报告目录：

`F:\ActionSKUTracker\runtime\db\reports\20260829T132648Z_b71f1151\`

- `migration_report.json`
- `validation_report.json`
- `mapping_summary.json`

staging 文件位于：

`F:\ActionSKUTracker\runtime\db\staging\20260829T132648Z_b71f1151\action_tracker.staging.db`

## 6. 结论与未授权事项

SQLite Mirror V1 已能作为可复核的数据基础层使用，但仍是 Mirror/Validation 层，Excel Master 仍是生产 Source of Truth。尚未授权：SQLite 反向写 Master、每日运行直接写 DB、官网采集改由 DB 驱动、字典或生命周期切换到 DB。
