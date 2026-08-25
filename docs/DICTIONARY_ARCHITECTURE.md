# Action 本地字典与功能模块边界

## 定位

本地字典是商品身份和标准化层，不替代生命周期 Master、运行 snapshot 或官网西语事实。它保存稳定的命名、品牌、分类和术语；价格、在售状态、缺失次数、促销事件和详情抓取状态仍由现有运行系统负责。

## 字典文件

运行时生成在 `runtime/dictionary/`：

- `product_dictionary.csv`：SKU 级标准化商品记录，以 `sku` 为主键；含来源哈希、首次/最后观察日期。
- `brand_dictionary.csv`：品牌标准名、别名和人工确认状态。
- `category_dictionary.csv`：西语类目到 15 个中文一级类目的映射关系。
- `term_dictionary.csv`：材质、颜色、单位、包装等固定译法。
- `source_damage_report.csv`：历史西语源字段损坏清单；`SOURCE_DAMAGED` 表示缺少可信原始证据，`SOURCE_POLLUTED` 表示网页 UI 文案错位进入商品字段；两者都不允许反向翻译填充。
- `manual_overrides.csv`：字段级人工覆盖、原因、来源和锁定状态；同一 SKU 的同一字段只能有一条有效覆盖。
- `build_manifest.json`：本次构建使用的 Master 哈希、SKU 数量、schema 版本与是否实际改写文件。

经过审计的正式基线发布在 `data/dictionary/`，由
`scripts/publish_dictionary_baseline.py` 从运行时字典校验后复制生成；新工作区在
`runtime/dictionary/` 尚不存在时，会优先使用该基线初始化。运行时快照、备份、审计报告和临时复核包仍不进入 Git。
发布脚本会先重新运行审计；出现 FAIL 时直接阻断，不生成新的基线。

运行时数据不进入 Git；`data/dictionary/` 只保存经过审计的稳定字典基线。代码、字段 schema、说明文档和基线清单进入 Git。

## 字段所有权

| 层 | 负责 | 不负责 |
| --- | --- | --- |
| 官网 / snapshot | 西语事实、采集日期、详情证据 | 中文标准化 |
| 生命周期 Master | 在售、NEW、REAPPEARED、缺失、价格和事件 | 自由翻译 |
| 本地字典 | SKU 名称、品牌、分类、术语、人工锁定 | 当日价格和上下架结论 |
| 导出 profile | Excel 字段顺序、文件名、带图/不带图 | 改写事实和字典 |

## 匹配优先级

人工锁定值 → 商品字典 → 品牌/分类/术语字典 → 模型翻译 → REVIEW_QUEUE。

人工锁定或已人工审核的字段不得被每日扫描或模型自动覆盖。人工覆盖按字段生效，例如修改中文品名不会阻断类目映射更新。

## 状态

`UNTRANSLATED`、`LEGACY_UNVERIFIED`、`MODEL_TRANSLATED`、`RULE_NORMALIZED`、`NEEDS_REVIEW`、`HUMAN_REVIEWED`、`LOCKED`。旧表中只有“已有中文值”证据的记录统一标为 `LEGACY_UNVERIFIED`，不得伪称模型翻译或人工审核。

`LOCKED` 是字段级保护信号，不代表商品在售；在售状态必须读取生命周期 Master。

## 第一阶段已落地

`action_tracker.dictionary` 提供 CSV 主键/结构校验、原子写入与备份、字段级人工覆盖、来源哈希复核、保留历史 SKU，以及不清空人工二级分类的类目合并。`scripts/build_dictionary.py` 从 `08_LONG_TERM_MASTER` 的全部 `OFFICIAL_SKU` 建立长期底座，再以 CURRENT 补充当日字段；它只读取正式 Master，不修改 Master。

若 CSV 正被 Excel 占用，构建会保留 `*.pending-*.csv` 并报出待提交路径，原字典不会被截断或覆盖。每次构建先校验 CSV 的 schema 与唯一键；出现重复 SKU、重复分类关系或冲突人工覆盖时直接失败，必须人工修正后重跑。

术语初始种子位于 `config/dictionary_terms.yaml`，当前只收录高置信度规格、单位、材质和属性词；模糊词不自动替换商品字段。`scripts/build_spanish_reference.py` 可将历史西语 Raw 表与已有数值参考合并为运行时证据，构建阶段按字段选择最近的干净值。找不到可信来源的字段进入 `source_damage_report.csv`，不得用模型伪造官网西语。
