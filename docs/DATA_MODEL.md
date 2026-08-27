# Action SKU Tracker 数据模型

## 1. 通用类型

| 类型 | 规则 |
| --- | --- |
| SKU | Text；非空；同一数据集唯一；禁止科学计数法和猜测补全 |
| 日期 | 业务日期使用 `YYYY-MM-DD`；Excel 数据单元格写真实日期类型 |
| 时间 | ISO-8601，含时区 |
| 价格 | Number；不写带货币符号的文本；EUR 仅作为显示格式 |
| URL | 完整 HTTP(S) URL；不得翻译或拼接猜测 |
| 布尔证据 | 明确 true/false；历史 Presence Profile 使用数值 1/0 |
| 空值 | 真正空值；不得用 `N/A`、`None`、0 或猜测值代替 |

## 2. 字段所有权

| 字段族 | 权威层 | 允许的派生层 |
| --- | --- | --- |
| SKU、商品 URL | 官网事实 / Master | Export 只读 |
| 西语名称、类目、规格、描述、详情 | 官网事实 / Snapshot / Master | 字典只做中文派生 |
| 当前售价、原价、单价 | 官网事实 / Master | Export 格式化 |
| Presence 来源、CURRENT | 冻结的 Observation | Lifecycle / Export 消费 |
| NEW/MISSING/OFFLINE/REAPPEARED | Lifecycle | Master、事件和 Export 展示 |
| 中文名称、分类、规格、描述、详情 | Dictionary | Export 消费 |
| 品牌、术语、人工覆盖 | Dictionary | 中文标准化 |
| 历史日期 0/1 | 对应历史批次 SKU 集合 | Export 矩阵 |
| 图片 | 本地图片缓存 | 中文 Export 嵌入 |

字典和 Export 永远不能改写官网西语事实、价格或 Presence。

## 3. Observation 与 Snapshot

每次运行以 `run_id` 标识，至少包含：

- `run_date`、开始/结束时间、dry-run；
- Sitemap 是否有效；
- 各 Listing 入口覆盖状态；
- AccessController 最终状态；
- Presence SKU 集合和每 SKU 来源；
- Listing/Detail 字段来源；
- Detail 状态；
- QA 结果和 commit status。

商品标准化行需要显式区分：

- `presence_source`；
- `listing_fields_source`；
- `detail_fields_source`；
- `detail_status`：COMPLETE / INCOMPLETE / ACCESS_INTERRUPTED / PENDING。

由上一期带入的字段不得伪装成当天新抓取字段。

## 4. 生命周期实体

`known_skus.csv` 以 SKU 为主键，保存跨日状态所需的最小事实：

- first_seen / last_seen；
- previous_status / last_status；
- missing_count；
- last_valid_observation；
- 最近 Presence 来源；
- 必要的事件幂等标识。

核心状态：

| 状态 | 语义 |
| --- | --- |
| NEW | 第一次被有效观察到 |
| ACTIVE | 连续有效出现 |
| MISSING_FIRST | 第一次有效缺失 |
| MISSING_CONTINUED | 连续有效缺失但未达到 OFFLINE 阈值 |
| OFFLINE | 达到配置的有效缺失确认次数 |
| REAPPEARED | 历史 SKU 从 MISSING/OFFLINE 重新出现；事件后恢复 ACTIVE |
| UNKNOWN | 当前观测不完整，不推进跨日状态 |

FIRST_SEEN 是首次出现事件，不能与 REAPPEARED 同日同 SKU 共存。

## 5. Master

正式 Master：`runtime/master/Action_Master.xlsx`。

职责：

- `CURRENT` 只放当前有效商品；
- 长期商品表按 SKU 保留历史商品；
- 价格、事件、Review 和 Run Log 可审计；
- 正式写入必须经过 QA 并使用集中 Writer；
- QA FAIL / dry-run 不覆盖正式内容。

Master 是生产文件，不是字典仓库；中文标准化的长期主键和审核状态应保存在字典文件中。

## 6. 字典数据集

### product_dictionary.csv

SKU 主键，保存标准中文字段、对应西语来源、source hash、状态、首次/最后观察时间和更新时间。

### brand_dictionary.csv

品牌标准名、别名、确认状态和证据。品牌可以保留原文，不进行机械翻译。

### category_dictionary.csv

西语类目关系到 15 个固定中文一级类目及中文二级类目。关系键必须唯一。

### term_dictionary.csv

人工确认的西语术语、中文标准译法和 term type。未来若增加 category scope，必须迁移 schema 和 Profile 版本。

术语候选是独立运行证据，不等于正式术语：`term_es、suggested_zh、term_type、
occurrence_count、sku_count、cat1_distribution、sample_contexts、source_dates、
decision、review_status`。候选必须经人工批准后才可写入 `term_dictionary.csv`。

### manual_overrides.csv

同一 SKU、同一字段最多一条有效人工覆盖，记录值、原因、来源、锁定状态和时间。覆盖只保护目标字段。

### model_translation_overrides.csv

模型派生值必须带源字段 hash。当前西语源 hash 不一致时结果立即失效。

### source_damage_report.csv

记录 SOURCE_DAMAGED、SOURCE_POLLUTED 等源事实问题。中文覆盖不能“修复”缺失或污染的西语事实。

### baseline_manifest.json

记录 schema version、发布时间、各文件行数和 SHA-256。正式基线只有在审计通过后才能发布。

## 7. Review Queue

运行时唯一队列：`runtime/review_queue/review_queue.csv`。

必备字段：

```text
review_id, issue_type, sku, field, current_value, suggested_value,
evidence, reason, created_at, updated_at, status, resolution
```

`review_id` 由问题类型、SKU、字段、当前值、建议值和证据稳定计算。状态仅允许：PENDING、APPROVED、REJECTED、RESOLVED。

问题类型至少包括：BRAND_CANDIDATE、TERM_CANDIDATE、NAME_REVIEW、CATEGORY_REVIEW、SOURCE_HASH_CHANGED、MODEL_LOW_CONFIDENCE、SOURCE_DAMAGED、SOURCE_POLLUTED、DICTIONARY_CONFLICT。

## 8. Export 数据模型

Export 由以下实体组成：

- `ExportProfile`：模板 ID、版本、工作表、列和格式；
- `ExportSource`：业务日期、run_id、正式来源类型和 hash；
- `HistoryPresence`：SKU × 日期 → 0/1；
- `CatalogRowES`：当日官网事实；
- `CatalogRowZH`：同一 SKU 的中文派生字段；
- `ExportManifest`：数量、hash、图片和校验结果。

Template 1 的字段契约见 `EXPORT_PROFILE.md`。ES/ZH 必须共享同一正式 SKU 集合；中文缺失不能删除行。

## 9. 历史 Presence

历史 Presence 只能从 `config/history_sources.yaml` 指向的只读批次建立：

```text
presence[sku, date] = 1  当且仅当该日期源文件的唯一 SKU 集合包含 sku
presence[sku, date] = 0  否则
```

不得根据 first_seen、last_seen、当前 Master 或字典推断过去 Presence。同一批次重复行先按 SKU 去重，同时把原始行数、唯一 SKU 数和重复数写入 manifest。

## 10. 数据版本与迁移

- 结构变化必须提升 schema/profile version；
- 字段重命名要有显式迁移，不能静默复用旧列；
- SQLite 不是当前数据模型的生产存储；
- 任何迁移必须保留原文件、来源 hash、行数对账和可回滚证据。

## 11. CI 中的数据使用边界

CI 测试只使用仓库中的 schema/配置和临时生成的最小 fixture，不把 `runtime/`、正式 Master、State、字典运行区或历史源文件作为云端写入目标。CI 可以验证实体结构和对账规则，但不能替代真实来源的覆盖率、QA 或 Presence 证据。
