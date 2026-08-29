# ActionSKUTracker — Excel → SQLite Mirror V1 Mapping

状态：设计冻结；真实迁移已执行并通过技术 Gate，实际结果见 `SQLITE_MIRROR_VALIDATION.md`。
来源：`F:\ActionSKUTracker\runtime\master\Action_Master.xlsx`
原则：保留来源、保留原始值、拒绝猜测 SKU、拒绝静默去重。

## 1. 迁移策略代码

| 代码 | 含义 |
| --- | --- |
| `MIGRATE` | 进入 SQLite 正式 Mirror 表 |
| `DERIVE` | 从来源安全派生，例如规范化数值或视图 |
| `PARITY_ONLY` | 只用于集合/字段对账，不复制成独立事实表 |
| `AUDIT_ONLY` | 保留在迁移审计报告或证据层，不进入业务表 |
| `DEFER` | 先保留源证据，待语义确认 |
| `SOURCE_ISSUE` | 源数据本身有问题，不能静默修正 |
| `DO_NOT_MIGRATE` | 不写入 Mirror |

## 2. Sheet 级映射

| Excel Sheet | 目标 | 规则 |
| --- | --- | --- |
| `01_SKU_ZH_CURRENT` | `PARITY_ONLY` + `product_localizations` 对账 | 不建 `products_zh`；用于确认中文 CURRENT SKU 集合和字段来源 |
| `02_SKU_ES_CURRENT` | `PARITY_ONLY` + `products`/localization 对账 | 不建 `products_es`；西语事实只进入统一 `products` |
| `03_PRICE_HISTORY` | `price_history` | 14,042 条有正式 SKU 的记录可迁移；970 条空 SKU 仅审计保留 |
| `04_EVENT_HISTORY` | `events` | 21,000 条有正式 SKU 的记录可迁移；1,940 条空 SKU 仅审计保留 |
| `05_RUN_LOG` | `runs` | 18 条全部迁移，未出现空 Run ID |
| `06_REVIEW_QUEUE` | `reviews`（Master 审计语义） | 516 条全部按来源行迁移；25 条重复业务候选键保留，不先去重；不导入运行时 Review Queue |
| `07_APRIL_ARCHIVE` | `AUDIT_ONLY` | 四月原始归档作为证据，不能重复成为 products/price/events |
| `08_LONG_TERM_MASTER` | `products` + unmatched audit | 8,680 条正式 SKU 迁移；900 条无正式 SKU 进入 unmatched audit，不生成 SKU |
| `09_APRIL_MATCH_AUDIT` | `AUDIT_ONLY` / migration audit | 只保存匹配方法、候选、置信度和证据，不覆盖 products |
| `10_SOURCE_SCHEMA` | `schema_metadata` + source manifest | 19 条全部保留；重复文件+Sheet 用 source row 区分 |

除业务目标表外，10 张 Sheet 的所有非空源行统一写入 `source_records`，以
`(migration_id, source_sheet, source_row_no)` 为稳定身份，保存完整 `raw_json` 和
`raw_hash`。因此 `AUDIT_ONLY` 或 `SOURCE_ISSUE` 只表示业务解释策略，不表示原始行被丢弃。

## 3. 字段映射

### 3.1 `08_LONG_TERM_MASTER` → `products`

| Excel 列 | DB 列 | 处理 |
| --- | --- | --- |
| `正式SKU` | `products.sku` | `MIGRATE`；非空才允许进入 products |
| `实体ID` | `products.canonical_id` | `MIGRATE`；唯一约束 |
| `西班牙语品名` | `name_es` | 原文迁移 |
| `一级类目（西语）` | `cat1_es` | 原文迁移 |
| `二级类目（西语）` | `cat2_es` | 原文迁移 |
| `规格（西语）` | `spec_es` | 原文迁移 |
| `商品链接` | `product_url` | 原文迁移 |
| `当前售价 (€)` | `current_price` | 数值直接迁移；无法解析时保留源值并报告 |
| `历史最低价 (€)` | `historical_min_price` | 数值直接迁移 |
| `历史最高价 (€)` | `historical_max_price` | 数值直接迁移 |
| `当前状态` | `current_status_raw` | 保留原值；ACTIVE 视图映射另行校验 |
| `首次观察日期` | `first_seen_at` | 日期规范化为 ISO，保留源行 |
| `最后观察日期` | `last_seen_at` | 日期规范化为 ISO，保留源行 |
| `来源工作表` | `source_sheet` | 固定来源追溯 |
| Excel 行号 | `source_row_no` | 来源定位唯一键 |
| `中文品名`、中文类目、中文规格 | `product_localizations` | 不写入 products 西语事实列 |
| `四月归档ID集合`、`来源数`、`核对备注` | migration audit metadata | 不丢失，但不作为商品身份判断 |

### 3.2 `01_SKU_ZH_CURRENT` → `product_localizations` / parity

| Excel 列 | DB 列 | 处理 |
| --- | --- | --- |
| `SKU` | localization.sku / current parity | 必须命中 products |
| `中文品名` | localization.name | `source='MASTER_01_ZH_CURRENT'` |
| `一级类目（中文）` | localization.cat1 | 原值保留 |
| `二级类目（中文）` | localization.cat2 | 原值保留 |
| `规格（中文）` | localization.spec | 原值保留 |
| `中文描述` | localization.description | 原值保留 |
| `中文产品详情` | localization.details | 原值保留 |
| `翻译状态`、`匹配状态` | localization.review_status / source metadata | 不改变状态语义 |
| 西语事实、价格、链接、状态 | products / parity | 与 `02_SKU_ES_CURRENT` 对账，不由中文覆盖 |

### 3.3 `02_SKU_ES_CURRENT` → `products` / parity

`08_LONG_TERM_MASTER` is the long-term identity/history baseline. For SKUs present in
`02_SKU_ES_CURRENT`, that sheet is authoritative for the current Spanish fact fields
`name_es`, `cat1_es`, `cat2_es`, `spec_es`, `description_es`, `details_es`, `image_url`,
`current_price`, and `product_url`. It must not overwrite `canonical_id`, historical
min/max prices, lifecycle status, first/last-seen dates, or provenance columns from the
long-term baseline. SKUs absent from the current sheet retain their 08 values.

| Excel 列 | DB 列 | 处理 |
| --- | --- | --- |
| `SKU` | products.sku | 必须命中 products |
| `Canonical_ID` | products.canonical_id | 与长期表对账 |
| `西班牙语品名` | products.name_es | 当前 SKU 以 02 为权威；历史保留 08 |
| `一级类目（西语）`、`二级类目（西语）` | products.cat1_es/cat2_es | 当前 SKU 以 02 为权威；历史保留 08 |
| `规格（西语）` | products.spec_es | 当前 SKU 以 02 为权威；历史保留 08 |
| `当前售价 (€)`、`原价 (€)`、`上次售价 (€)` | products/price history | current/original 进入事实或审计；历史变价以 03 为准 |
| `商品链接`、`图片链接` | products.product_url/image_url | 原文迁移 |
| `描述（西语）`、`产品详情（西语）` | product source evidence | 本 V1 可放 raw source / product detail extension；不翻译 |
| `当前状态`、标签列 | products status/badges | 原值保留，生命周期算法不在迁移中重算 |

### 3.4 `03_PRICE_HISTORY` → `price_history`

有正式 SKU 的行按以下映射：

| Excel 列 | DB 列 | 处理 |
| --- | --- | --- |
| `SKU` | `sku` | 必须存在 products |
| `Canonical_ID` | `canonical_id` | 可为空但原值保留 |
| `日期` | `observed_at` | ISO 日期 |
| `旧售价 (€)` | `previous_price` | 可解析则 REAL；原始行放 raw_json |
| `新售价 (€)` | `new_price` | 可解析则 REAL；空值不得伪造 |
| `原价 (€)` | `original_price` | 同上 |
| `变化类型` | `change_type` | 原值迁移 |
| `促销状态` | `promotion_raw` | 原值迁移 |
| `来源文件`、`来源Sheet` | source columns | 原值迁移 |

970 条 `SKU` 为空的行必须使用 `Canonical_ID + source row` 进入 unmatched audit，不能用 `ACTU...` 之类规则生成正式 SKU。

### 3.5 `04_EVENT_HISTORY` → `events`

`Canonical_ID`、`SKU`、`日期`、`事件类型`、`旧值`、`新值`、`来源文件`、`备注` 逐字段迁移。21,000 条有正式 SKU 的行进入 events；1,940 条空 SKU 行只进 unmatched audit。迁移不重算 FIRST_SEEN/REAPPEARED，也不修改事件类型。

### 3.6 `05_RUN_LOG` → `runs`

`Run ID` → `run_id`；`运行日期` → `run_date`；开始/结束时间、各数量、访问状态、QA 状态、运行状态和备注逐字段迁移。无法从原表证明的 Observation 细节不填入 observations。

### 3.7 `06_REVIEW_QUEUE` → `reviews`

源表没有 `review_id`。使用 `MASTER06:<source_row_no>` 生成来源稳定 ID，按行保留 516 条；`SKU` 通过 FK 校验时必须命中 products，`日期`、问题类型、证据、候选值、置信度、建议动作、人工备注逐字段迁移。25 条重复候选键标记 `SOURCE_DUPLICATE`，不得静默合并。

### 3.8 `10_SOURCE_SCHEMA` → `schema_metadata`

日期、文件名、Sheet、Raw 行数、Raw 列数、真实 Raw Schema、来源作用、数据状态、备注全部保留。由于 `文件名 + Sheet` 有 1 组重复，使用来源行号作为附加身份。

## 4. 不迁移或不重算的内容

- 不从历史文件“没看到 SKU”推导 Presence=0；
- 不从中文字段反推西语事实；
- 不在迁移阶段修复价格、生命周期、翻译或类别错误；
- 不把 900 待匹配实体、970 空 SKU 价格行、1,940 空 SKU 事件行伪造为正式商品历史；
- 不把 `07_APRIL_ARCHIVE` 和 `09_APRIL_MATCH_AUDIT` 重复写成业务事实；
- 不让 SQLite 反写 Excel Master 或参与 daily-run。
