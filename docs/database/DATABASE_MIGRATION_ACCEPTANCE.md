# ActionSKUTracker — SQLite Mirror V1 Acceptance

状态：技术 Gate 已通过；最终判定为 `SQLITE MIRROR VALIDATED WITH SOURCE DATA ISSUES`。
验收对象：Excel Master → staging SQLite → validation → Mirror DB。
生产边界：Excel Master 仍是唯一正式 Source of Truth。

## 1. 输入基线

| 项目 | 基线 |
| --- | --- |
| Master | `F:\ActionSKUTracker\runtime\master\Action_Master.xlsx` |
| Master SHA-256 | `a1a5adde31225b093b627cd1e2413c369b5ea9b737ef428b50b13f21d03c5da3` |
| ZH CURRENT | 5,431 个唯一 SKU |
| ES CURRENT | 5,431 个唯一 SKU |
| LONG_TERM formal SKU | 8,680 个唯一正式 SKU |
| LONG_TERM unmatched | 900 条无正式 SKU 实体 |
| PRICE_HISTORY | 15,012 行；14,042 有正式 SKU；970 行空 SKU |
| EVENT_HISTORY | 22,940 行；21,000 有正式 SKU；1,940 行空 SKU |
| RUN_LOG | 18 行 |
| Master REVIEW_QUEUE | 516 行；25 条重复候选键 |
| SOURCE_SCHEMA | 19 行；1 组重复文件+Sheet |

## 2. 数据库文件与安全模式

- staging：`runtime/db/staging/<migration_id>/action_tracker.staging.db`
- Mirror：`runtime/db/action_tracker.db`
- 报告：`runtime/db/reports/<migration_id>/`
- 生产 DB、WAL、SHM 和 backup 不进入 Git。
- DB 命令只读 Master，禁止写 Master、State、Dictionary，禁止访问官网、运行 daily-run 或开启 Dictionary Apply。
- 建库必须执行 `PRAGMA foreign_keys = ON`、`PRAGMA integrity_check` 和 `PRAGMA foreign_key_check`。

## 3. 必须通过的 Gate

### 3.0 Schema identity / legacy gate

- `schema_family` 必须为 `ACTION_SQLITE_MIRROR`，`schema_version` 必须为 `1.0.0`；
- 缺少 V1 身份列或带有早期脚手架形状的 DB 必须被识别为 `LEGACY`，`db-init` 返回 `LEGACY_DB_REBUILD_REQUIRED`，不允许原地升级；
- 空库/新库 `db-init` 可重复执行，V1 库重复执行不改变结构。

### 3.1 Schema Gate

- 所有 V1 表创建成功：`products`、`product_localizations`、`observations`、`price_history`、`events`、`runs`、`reviews`、`schema_metadata`、`migration_runs`；
- Schema version = `1.0.0`；
- 所有主键、唯一键、外键和 CHECK 约束可通过 fixture 测试；
- 每次连接 foreign keys 实际为 `1`。

### 3.2 Product Gate

- `products` = 8,680 条正式 SKU；
- `sku` 非空、唯一；`canonical_id` 非空、唯一；
- 900 条无正式 SKU 不进入 products；
- 不改变西语事实、价格或状态语义。

### 3.3 CURRENT Exact Set Gate

必须同时满足：

```text
ZH_CURRENT SKU set
= ES_CURRENT SKU set
= DB current SKU set
```

期望：每个集合 5,431 个，差异集合为空。不能只比较行数。

### 3.4 History Gate

| 来源 | 目标 | 必须验证 |
| --- | --- | --- |
| `03_PRICE_HISTORY` 有 SKU 14,042 行 | `price_history` | 行数、SKU 关联、原始值保留；970 空 SKU 进入 audit，不得伪造 |
| `04_EVENT_HISTORY` 有 SKU 21,000 行 | `events` | 行数、SKU 关联、事件类型原值；1,940 空 SKU 进入 audit |
| `05_RUN_LOG` 18 行 | `runs` | run_id 唯一、行数一致 |
| `06_REVIEW_QUEUE` 516 行 | `reviews` | 全部保留，重复候选键不静默去重 |

### 3.5 Integrity Gate

- `PRAGMA integrity_check` 必须返回 `ok`；
- `PRAGMA foreign_key_check` 必须返回空；
- orphan products/history/localizations/observations = 0；
- 任何主键、外键、唯一键违规都必须使迁移 FAIL 并回滚。

### 3.6 Transaction/Rollback Gate

- 导入顺序为 BEGIN → products → localizations → observations/history/events/runs/reviews → audit → validate → COMMIT；
- 任意一步故意失败时，staging DB 必须回滚，不出现半迁移数据；
- 只有所有验证 PASS 才能用 staging DB 替换 Mirror；
- 替换失败必须保留旧 Mirror，并在 migration report 记录 rollback 状态。

### 3.7 Field parity / raw evidence gate

- `products`、`product_localizations`、`price_history`、`events`、`runs`、`reviews` 均做逐字段对账；每类最多输出 20 条 mismatch 样本，任何 mismatch 都使验证 FAIL；
- `source_records` 必须覆盖 10 张 Sheet 的每一条非空源行，`(Sheet, row_no)` 精确一致，`raw_json` 与 `raw_hash` 一致；原始证据层不与 source issues 混淆。

### 3.8 Promotion safety gate

- staging 校验通过后，立即重新计算 Master SHA-256；若与迁移前不同，禁止替换旧 Mirror；
- 原子替换后执行完整性、外键、schema family/version、migration_id 最小验证；失败时从备份恢复旧 Mirror。

### 3.9 Read-only Source Gate

迁移前后计算 Master SHA-256：

```text
MASTER_HASH_BEFORE == MASTER_HASH_AFTER
```

同时确认：

- known_skus unchanged；
- offline_skus unchanged；
- Dictionary unchanged；
- `F:\按日期整理` 未被写入；
- 未访问 Action 官网；
- 未执行正式 daily-run；
- 未反写 Excel。

## 4. 报告要求

每个 migration 必须生成：

- `migration_report.json`：输入路径/hash、时间、各表源/目标行数、迁移策略、源问题；
- `validation_report.json`：PK/FK、CURRENT exact set、历史 parity、integrity、rollback 和 Master hash；
- `mapping_summary.json`：每个 Sheet/字段的 MIGRATE、DERIVE、PARITY_ONLY、AUDIT_ONLY、DEFER、SOURCE_ISSUE 统计。

报告必须明确区分：

- `SOURCE_DUPLICATE`：源表重复，不是迁移器偷偷去重；
- `UNMATCHED_CANONICAL`：有 Canonical_ID 但无正式 SKU；
- `CURRENT_PARITY_FAIL`：集合不一致；
- `ORPHAN_HISTORY`：历史行无法关联 products。

## 5. Fixture 测试清单

测试必须 offline、使用 tmp_path/fixture，不读取真实 Master/runtime，不访问官网：

1. Schema 全表创建与 schema_version；
2. foreign_keys 开启；
3. 重复 SKU 拒绝；
4. orphan price/event/observation/localization 拒绝；
5. 重复 run_id/review_id 拒绝或按来源行身份保留；
6. transaction 中途失败完整 rollback；
7. ZH/ES/DB CURRENT exact set 一致；
8. 少一个 SKU 时 parity FAIL；
9. 空 SKU 历史进入 audit，不生成伪 SKU；
10. Master hash 前后一致；
11. `integrity_check=ok`；
12. `foreign_key_check` 为空；
13. 缺 Sheet/缺列/非法 Excel 明确失败；
14. 中文修改不改变西语事实；
15. 迁移重复执行结果确定且不产生重复 products/history。
16. 六类业务表逐字段篡改会被 parity gate 拒绝；
17. source_records 行删除、raw_json/hash 篡改会被 evidence gate 拒绝；
18. Master final hash 变化、promotion 后最小校验失败均保留旧 Mirror；
19. 旧库形状明确拒绝，重复身份、缺 Sheet、缺列、外键孤儿明确失败。

## 6. 最终判定

只允许以下四种结果：

- `SQLITE MIRROR VALIDATED`：所有 Gate 通过且无源数据问题；
- `SQLITE MIRROR VALIDATED WITH SOURCE DATA ISSUES`：数据库一致性通过，但报告保留源重复/待匹配问题；
- `SQLITE MIRROR REQUIRES FIXES`：迁移或对账 Gate 未通过；
- `SQLITE MIRROR UNSAFE`：出现生产副作用、伪造身份、丢失源数据或无法回滚。

实际迁移已将 900 条待匹配实体、970 条空 SKU 价格行、1,940 条空 SKU 事件行、审计表记录、Review/Source Schema 重复行列入 Source Data Issues；技术 Gate 通过，但不能将其伪装成无源问题 PASS。详见 [SQLITE_MIRROR_VALIDATION.md](SQLITE_MIRROR_VALIDATION.md)。
