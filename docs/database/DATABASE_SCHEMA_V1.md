# ActionSKUTracker — SQLite Data Foundation V1 Schema

状态：设计冻结，尚未接管生产。
数据库目标：`F:\ActionSKUTracker\runtime\db\action_tracker.db`
Schema 版本：`1.0.0`

## 1. 总体原则

- SQLite 只是从正式 Excel Master 建立的只读 Mirror 和迁移验证对象。
- Excel Master 仍是生产 Source of Truth；本阶段禁止 SQLite → Excel、Dual Write、daily-run 写 SQLite。
- 迁移优先 `lossless before normalization`：原始文本和来源信息要保留，安全的数值规范化另存，不覆盖原值。
- 只有有正式 SKU 的记录才能进入 `products` 及其正式关系表。900 条四月待匹配实体不生成 SKU。
- 每次连接开启 `PRAGMA foreign_keys = ON`；全部导入在 staging DB 单事务中完成，验证失败必须 ROLLBACK。

## 2. 表结构

### 2.1 `products`

一行一个正式长期 SKU。来自 `08_LONG_TERM_MASTER` 中 `正式SKU` 非空的 8,680 行；900 条无正式 SKU 的实体不进入本表。

| 列 | 类型 | 空值 | 约束 | 来源/含义 |
| --- | --- | --- | --- | --- |
| `sku` | TEXT | 否 | PRIMARY KEY | 正式 SKU；原样保留，不猜测补全 |
| `canonical_id` | TEXT | 否 | UNIQUE | 长期实体稳定 ID |
| `name_es` | TEXT | 是 |  | 西语品名事实 |
| `cat1_es` | TEXT | 是 |  | 西语一级类目事实 |
| `cat2_es` | TEXT | 是 |  | 西语二级类目事实 |
| `spec_es` | TEXT | 是 |  | 西语规格事实 |
| `product_url` | TEXT | 是 |  | 官网商品链接 |
| `image_url` | TEXT | 是 |  | 官网图片链接 |
| `current_price` | REAL | 是 |  | 可安全解析的当前/最后价格；原始文本另存来源证据 |
| `historical_min_price` | REAL | 是 |  | 长期历史最低价 |
| `historical_max_price` | REAL | 是 |  | 长期历史最高价 |
| `current_status_raw` | TEXT | 是 |  | Master 原始状态值 |
| `first_seen_at` | TEXT | 是 |  | 首次观察日期，ISO `YYYY-MM-DD` |
| `last_seen_at` | TEXT | 是 |  | 最后观察日期，ISO `YYYY-MM-DD` |
| `source_sheet` | TEXT | 否 |  | 固定为 `08_LONG_TERM_MASTER` |
| `source_row_no` | INTEGER | 否 | UNIQUE(source_sheet, source_row_no) | 原始行号，便于回溯 |
| `created_at` | TEXT | 否 |  | Mirror 写入时间 |
| `updated_at` | TEXT | 否 |  | Mirror 更新时间 |

### 2.2 `product_localizations`

中文是派生层，不与西语事实混表。第一版支持 `language='zh'`，主键为 `(sku, language)`。

| 列 | 类型 | 空值 | 约束 | 来源/含义 |
| --- | --- | --- | --- | --- |
| `sku` | TEXT | 否 | FK → products.sku | 商品身份 |
| `language` | TEXT | 否 | PRIMARY KEY part | `zh` |
| `name` | TEXT | 是 |  | 中文品名 |
| `cat1` | TEXT | 是 |  | 中文一级类目 |
| `cat2` | TEXT | 是 |  | 中文二级类目 |
| `spec` | TEXT | 是 |  | 中文规格 |
| `description` | TEXT | 是 |  | 中文描述 |
| `details` | TEXT | 是 |  | 中文产品详情 |
| `source` | TEXT | 否 |  | 字典/人工覆盖/模型/历史 Master 等来源 |
| `source_hash` | TEXT | 是 |  | 对应西语事实 hash 或字典源 hash |
| `review_status` | TEXT | 否 |  | `AUTO_READY` / `REVIEW_REQUIRED` / `SOURCE_BLOCKED` 等 |
| `updated_at` | TEXT | 否 |  | 最近派生更新时间 |

### 2.3 `observations`

逐 Run Presence 证据。当前 Master 没有完整 Observation Sheet，因此 V1 不伪造历史观察；没有真实 run+SKU 证据的行不写入本表。

| 列 | 类型 | 空值 | 约束 | 含义 |
| --- | --- | --- | --- | --- |
| `run_id` | TEXT | 否 | PRIMARY KEY part | 对应一次运行 |
| `sku` | TEXT | 否 | PRIMARY KEY part, FK | 商品身份 |
| `observation_date` | TEXT | 否 |  | 业务日期 |
| `presence` | INTEGER | 否 | CHECK 0/1 | 有效 Presence |
| `source_listing` | INTEGER | 是 |  | Listing 证据 |
| `source_sitemap` | INTEGER | 是 |  | Sitemap 证据 |
| `source_nuevo` | INTEGER | 是 |  | Nuevo 补充证据 |
| `source_promo` | INTEGER | 是 |  | Promoción 补充证据 |
| `current_price` | REAL | 是 |  | 当次价格 |
| `original_price` | REAL | 是 |  | 当次原价 |
| `observation_complete` | INTEGER | 否 | CHECK 0/1 | 观测是否完整 |
| `raw_json` | TEXT | 否 |  | 原始观察证据 |
| `created_at` | TEXT | 否 |  | Mirror 写入时间 |

### 2.4 `price_history`

来自 `03_PRICE_HISTORY` 的有正式 SKU 记录。保留 `current_price_raw`、`original_price_raw`、`unit_price_raw` 等原始字段；不能因解析失败删除记录。

| 列 | 类型 | 空值 | 约束 | 含义 |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | 否 | PRIMARY KEY AUTOINCREMENT | 数据库内部行 ID |
| `sku` | TEXT | 否 | FK → products.sku | 正式 SKU |
| `canonical_id` | TEXT | 是 |  | 来源 Canonical_ID |
| `observed_at` | TEXT | 否 |  | 业务日期 |
| `run_id` | TEXT | 是 |  | 如能从来源确定则关联 runs |
| `previous_price` | REAL | 是 |  | 规范化旧价 |
| `new_price` | REAL | 是 |  | 规范化新价 |
| `original_price` | REAL | 是 |  | 规范化原价 |
| `unit_price_raw` | TEXT | 是 |  | 单价原文 |
| `change_type` | TEXT | 否 |  | INITIAL/UP/DOWN 等原值 |
| `raw_json` | TEXT | 否 |  | 完整源行 |
| `source_file` | TEXT | 否 |  | 来源文件 |
| `source_sheet` | TEXT | 否 |  | 来源 Sheet |

### 2.5 `events`

来自 `04_EVENT_HISTORY` 的有正式 SKU 事件。事件记录历史事实，不重新发明生命周期算法。

| 列 | 类型 | 空值 | 约束 | 含义 |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | 否 | PRIMARY KEY AUTOINCREMENT | 数据库内部行 ID |
| `sku` | TEXT | 否 | FK → products.sku | 正式 SKU |
| `canonical_id` | TEXT | 是 |  | 来源 Canonical_ID |
| `occurred_at` | TEXT | 否 |  | 事件日期 |
| `run_id` | TEXT | 是 |  | 如能从来源确定则关联 runs |
| `event_type` | TEXT | 否 |  | FIRST_SEEN/LAST_SEEN/NEW/REAPPEARED 等原值 |
| `old_value` | TEXT | 是 |  | 原事件值 |
| `new_value` | TEXT | 是 |  | 新事件值 |
| `evidence` | TEXT | 是 |  | 备注/证据 |
| `source_file` | TEXT | 否 |  | 来源文件 |
| `source_sheet` | TEXT | 否 |  | 来源 Sheet |

### 2.6 `runs`

对应 `05_RUN_LOG`，`run_id` 为主键，所有真实字段保留；未在来源中的字段保持 NULL，不推断。

| 列 | 类型 | 空值 | 约束 | 含义 |
| --- | --- | --- | --- | --- |
| `run_id` | TEXT | 否 | PRIMARY KEY | 运行 ID |
| `run_date` | TEXT | 否 |  | 运行日期 |
| `started_at` | TEXT | 是 |  | 开始时间 |
| `finished_at` | TEXT | 是 |  | 结束时间 |
| `sitemap_count` | INTEGER | 是 |  | Sitemap 数 |
| `listing_count` | INTEGER | 是 |  | Listing 数 |
| `current_count` | INTEGER | 是 |  | CURRENT 数 |
| `new_count` | INTEGER | 是 |  | NEW 数 |
| `reappeared_count` | INTEGER | 是 |  | REAPPEARED 数 |
| `missing_first_count` | INTEGER | 是 |  | MISSING_FIRST 数 |
| `missing_continued_count` | INTEGER | 是 |  | MISSING_CONTINUED 数 |
| `offline_count` | INTEGER | 是 |  | OFFLINE 数 |
| `access_state` | TEXT | 是 |  | 访问状态 |
| `observation_complete` | INTEGER | 是 |  | 观测完整性 |
| `qa_status` | TEXT | 是 |  | QA 状态 |
| `commit_status` | TEXT | 是 |  | 提交状态 |
| `snapshot_path` | TEXT | 是 |  | 证据目录 |
| `source_row_no` | INTEGER | 否 | UNIQUE | 原始行号 |

### 2.7 `reviews`

只表示 Master 的 `06_REVIEW_QUEUE` 审计语义，不导入运行时 `runtime/review_queue/review_queue.csv`。由于源表没有 `review_id`，使用 `MASTER06:<source_row_no>` 作为来源稳定 ID，并保留所有重复源行。

| 列 | 类型 | 空值 | 约束 | 含义 |
| --- | --- | --- | --- | --- |
| `review_id` | TEXT | 否 | PRIMARY KEY | 来源行稳定 ID |
| `sku` | TEXT | 是 | FK → products.sku | SKU 可为空但当前源表实际非空 |
| `review_date` | TEXT | 是 |  | 日期 |
| `issue_type` | TEXT | 否 |  | 问题类型 |
| `evidence` | TEXT | 是 |  | 证据 |
| `candidate_value` | TEXT | 是 |  | 候选值 |
| `confidence` | REAL | 是 |  | 置信度 |
| `suggested_action` | TEXT | 是 |  | 建议动作 |
| `manual_note` | TEXT | 是 |  | 人工备注 |
| `source_row_no` | INTEGER | 否 | UNIQUE | 原始行号 |

### 2.8 `schema_metadata`

保存 `schema_version`、Master 路径、Master SHA-256、来源 Sheet 和 Raw Schema 等元数据。重复 `文件名 + Sheet` 不能覆盖，使用来源行号区分。

### 2.9 `migration_runs`

保存每次 Excel → staging SQLite 的迁移批次：`migration_id`、source path/hash、时间、状态、各表数量、parity/integrity/FK 结果、报告路径和 rollback 状态。

## 3. Views

V1 至少提供：

- `v_current_products_es`：从 `products` 读取当前状态及西语事实；
- `v_current_products_zh`：`products` LEFT JOIN `product_localizations` 的 `language='zh'`；
- `v_db_current_skus`：只返回当前有效 SKU，用于和两个 CURRENT Sheet 做 exact set parity。

视图的当前状态映射必须以 `08_LONG_TERM_MASTER.当前状态` 的真实值为准，不能仅凭中文表或是否有翻译判断。

## 4. 与现有脚手架的关系

当前 `src/action_tracker/database/` 中已有早期 `products/product_observations/translations/image_map/runs/sync_queue` 脚手架，但：

- 字段命名、外键、源行追溯、迁移事务和审计表尚未满足本 V1；
- 不能直接把现有 `import_baseline()` 当成 Mirror 迁移完成；
- V1 实现时应以本文件和 Mapping/Acceptance 为契约，必要时保留兼容迁移，不删除现有生产链。
