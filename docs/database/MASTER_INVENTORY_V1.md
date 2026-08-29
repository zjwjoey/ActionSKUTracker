# ActionSKUTracker — Master Inventory V1

生成日期：2026-08-29 20:37（Asia/Shanghai）
正式 Master：`F:\ActionSKUTracker\runtime\master\Action_Master.xlsx`
Master SHA-256：`a1a5adde31225b093b627cd1e2413c369b5ea9b737ef428b50b13f21d03c5da3`

本清单基于当前正式 Master 的实际内容生成。它是 SQLite Mirror V1 的输入盘点，不改变 Master，不代表 SQLite 已接管生产。

## 1. Sheet 总览

| Sheet | 数据行数 | 列数 | 候选主键 | 重复键行数 | 空键行数 | 数据角色 | 数据库去向 | 迁移策略 |
| --- | ---: | ---: | --- | ---: | ---: | --- | --- | --- |
| `01_SKU_ZH_CURRENT` | 5,431 | 35 | `SKU` | 0 | 0 | 当前中文展示/派生视图 | `v_current_products_zh` | MIGRATE + PARITY_ONLY |
| `02_SKU_ES_CURRENT` | 5,431 | 30 | `SKU` | 0 | 0 | 当前西语事实视图 | `v_current_products_es` | MIGRATE + PARITY_ONLY |
| `03_PRICE_HISTORY` | 15,012 | 12 | `SKU + 日期` | 0 | 970 | 价格变更历史 | `price_history` | MIGRATE_LOSSLESS |
| `04_EVENT_HISTORY` | 22,940 | 8 | `SKU + 日期 + 事件类型` | 0 | 1,940 | 生命周期/标签事件历史 | `events` | MIGRATE_LOSSLESS |
| `05_RUN_LOG` | 18 | 24 | `Run ID` | 0 | 0 | 运行审计日志 | `runs` | MIGRATE_LOSSLESS |
| `06_REVIEW_QUEUE` | 516 | 8 | `SKU + 问题类型 + 证据` | 25 | 0 | Master 生命周期审计队列 | 待语义确认；暂不与运行时 Review 混表 | DEFER_SEMANTIC_CHECK |
| `07_APRIL_ARCHIVE` | 5,993 | 20 | `四月归档ID` | 0 | 0 | 四月历史归档证据 | AUDIT_ONLY | AUDIT_ONLY |
| `08_LONG_TERM_MASTER` | 9,580 | 24 | `实体ID` | 0 | 0 | 长期商品实体主表 | `products`（正式 SKU） | MIGRATE_SPLIT_BY_STATUS |
| `09_APRIL_MATCH_AUDIT` | 5,993 | 22 | `四月归档ID` | 0 | 0 | 四月归档匹配审计 | migration audit / AUDIT_ONLY | AUDIT_ONLY |
| `10_SOURCE_SCHEMA` | 19 | 9 | `文件名 + Sheet` | 1 | 0 | 原始来源元数据与 Raw Schema | `schema_metadata` / source manifest | MIGRATE_METADATA |

注：表中数据行数不含表头；`08_LONG_TERM_MASTER` 的表头在第 7 行，其前面有标题和汇总区。

## 2. 当前商品集合

- `01_SKU_ZH_CURRENT`：5,431 个唯一 SKU，无空 SKU、无重复。
- `02_SKU_ES_CURRENT`：5,431 个唯一 SKU，无空 SKU、无重复。
- 两个 CURRENT 表的 SKU 集合必须在迁移验证阶段做 exact set 对账；当前两表行数和唯一键统计一致，但仍需在 SQLite validator 中逐 SKU 比较。
- 5,431 是当前有效在售集合，不是历史累计 SKU 数。

## 3. 长期商品实体

`08_LONG_TERM_MASTER` 实际有 9,580 条实体记录，`实体ID` 全部非空且唯一。其中：

- `正式SKU` 非空且唯一：8,680 个；
- `正式SKU` 为空：900 个，均属于四月待匹配历史实体，不得猜测或生成正式 SKU；
- 正式 SKU 记录应迁移到 `products`；900 条无正式 SKU 的实体只作为迁移审计/待匹配证据保留，不能进入正式商品主键表。

## 4. 历史表的源数据问题

### 4.1 价格历史

`03_PRICE_HISTORY` 有 970 行没有 `SKU`，但保留了 `Canonical_ID`、日期、价格和来源。这些行对应四月历史中尚未匹配正式 SKU 的实体。迁移时不能用 `Canonical_ID` 猜造 SKU，也不能静默丢弃；应进入 source/migration audit，或在有正式匹配证据后再导入正式 `price_history`。

### 4.2 事件历史

`04_EVENT_HISTORY` 有 1,940 行没有 `SKU`，同样是 970 个待匹配实体各自的 `FIRST_SEEN`/`LAST_SEEN` 证据。不得把这 1,940 行写成正式 SKU 事件；应保留原始证据并标记未匹配。

### 4.3 Master Review Queue

`06_REVIEW_QUEUE` 的候选复合键出现 25 条重复行、24 个重复键组。它与运行时 `runtime/review_queue/review_queue.csv` 不一定同一语义。V1 不应先去重后宣称 PASS；必须保留原始行并在 mapping/validation 报告中标记 `SOURCE_DUPLICATE`，同时单独确认它是否需要成为正式 `reviews` 表。

### 4.4 Source Schema

`10_SOURCE_SCHEMA` 的 `文件名 + Sheet` 候选键有 1 组重复：`20260405Action商品全量_西语版_不带图.xlsx + Sheet1`。迁移元数据时必须保留两条原始记录并使用稳定行标识或来源记录 ID，不能用复合键静默覆盖。

## 5. Sheet 到数据库的初步边界

### 直接迁移或派生

- `08_LONG_TERM_MASTER` 的正式 SKU 部分 → `products`；中文字段属于派生/展示字段，不能覆盖西语事实。
- `03_PRICE_HISTORY` 的已具备正式 SKU 的行 → `price_history`，保留原始价格文本字段并另存规范化数值（如可安全解析）。
- `04_EVENT_HISTORY` 的已具备正式 SKU 的行 → `events`，不重新发明生命周期算法。
- `05_RUN_LOG` → `runs`。
- `01/02 CURRENT` → 用于 CURRENT exact-set parity；V1 可保存迁移证据，但不把两张 CURRENT 复制为两张商品事实表。

### 审计或延后

- `06_REVIEW_QUEUE`：先和运行时 Review Queue 做语义确认，重复源行进入报告。
- `07_APRIL_ARCHIVE`：默认 AUDIT_ONLY，除非证明有未被长期表/价格/事件吸收的业务字段。
- `09_APRIL_MATCH_AUDIT`：默认 migration/audit evidence，不是业务事实表。
- `10_SOURCE_SCHEMA`：迁入元数据/来源 manifest，不是商品业务表。

## 6. 下一步前置结论

1. 现有 `src/action_tracker/database/` 是早期 SQLite 脚手架，表名和字段尚未满足本 V1 的正式契约，不能直接视为完成。
2. `products` 必须以正式 SKU 为业务主键；`Canonical_ID` 可作为稳定来源标识，但不能把待匹配实体伪装为正式 SKU。
3. V1 必须使用 staging DB、事务、外键、原始来源 hash 和 validation report；不能反写 Excel Master。
4. `06_REVIEW_QUEUE`、四月待匹配记录和 `10_SOURCE_SCHEMA` 的重复问题需在 Mapping/Acceptance 文档中明确为源数据问题，不能通过静默去重掩盖。
5. 数据库仍是 Mirror/Validation 层；当前生产 Source of Truth 仍是 Excel Master。
