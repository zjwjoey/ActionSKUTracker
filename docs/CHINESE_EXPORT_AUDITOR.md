# 中文导出表独立审计器

## 用途

`scripts/audit_chinese_export.py` 对同一批次的西班牙语事实表和中文导出表做只读对照。它不调用导出器、翻译器、修复器或本地 Qwen 模块，因此可以在导出完成后单独验证结果。

审计器输出三个文件：

- `summary.json`：SKU 数、字段错误率、SKU 问题率、发布阻断率和规则计数。
- `findings.jsonl`：每条问题包含 SKU、字段、规则、源值、目标值和建议责任代码文件。
- `codex_handoff.json`：Codex 下一步可直接读取的状态和修复入口。

## 执行

```powershell
$env:PYTHONPATH = "src"
python scripts/audit_chinese_export.py `
  --spanish "F:\ActionSKUTracker\runtime\exports\20261003Action商品全量_西班牙语版_不带图.xlsx" `
  --chinese "F:\ActionSKUTracker\runtime\exports\20261003Action商品全量_中文版_不带图.xlsx" `
  --category-dictionary "F:\ActionSKUTracker\runtime\dictionary\category_dictionary.csv" `
  --brand-dictionary "F:\ActionSKUTracker\runtime\dictionary\brand_dictionary.csv" `
  --output "F:\ActionSKUTracker\runtime\audits\20261003_chinese_export"
```

退出码为 `0` 表示没有任何审计发现；`2` 表示需要修复或人工处理。`content_error_rate` 现在表示去重后的字段错误率：唯一的 `(SKU, 字段)` 问题数除以可审计字段数，目标为 `< 0.10`。原来的 SKU 问题率保存在 `content_sku_error_rate`。`release_block_rate` 仍单独保留，避免把尚未批准的翻译误判成可以发布。

## 规则和代码定位

| 规则 | 检查内容 | 首要定位 |
|---|---|---|
| `SKU_SET_MISMATCH` | SKU 缺失、重复或多出 | `exporting/service.py` |
| `IDENTITY_FACT_MISMATCH` | 价格、链接、图片链接变化 | `exporting/service.py` |
| `SPANISH_CATEGORY_RESIDUAL` / `CATEGORY_MAPPING_MISMATCH` | 分类仍为西语或不符合分类字典 | `exporting/dictionary_join.py` |
| `NUMERIC_FACT_DROPPED` / `NUMERIC_FACT_ADDED` | 数字、数量、尺寸被删改或凭空增加 | `localization/qa.py`、`localization/planner.py` |
| `FIELD_CROSS_FIELD_FACT` | 事实从详情/规格串到了别的字段 | `localization/planner.py` |
| `TECHNICAL_TOKEN_DROPPED` | 型号、认证、接口、尺寸标准等技术词丢失 | `localization/qa.py` |
| `SEMANTIC_FACT_DROPPED` | 材质、认证、香型等受控事实丢失 | `localization/qa.py` |
| `DETAIL_TERM_MISMATCH` | 详情键值的确定性术语错误 | `localization/formatter.py` |
| `OFFICIAL_TAG_DROPPED` | 官网官方标签没有进入备注 | `exporting/dictionary_join.py` |
| `UNIT_PRICE_TOKEN_BOUNDARY` | `€/lav` 被错误截成 `€/l` | `localization/formatter.py` |
| `SOURCE_ANOMALY_UNDECLARED` | 源站异常值被伪装成正常事实 | `localization/resolver.py` |
| `TEXT_CORRUPTION` | 残留占位符、断句、重复连接词 | `localization/qa.py` |
| `EMPTY_SOURCE_TARGET_NONEMPTY` | 西语源字段为空，中文却生成内容 | `database/production.py` |
| `PENDING_RELEASE_STATUS` | 中文字段仍带待审核状态 | `exporting/service.py` |
| `BRAND_DISPLAY_RESIDUAL` | 已确认品牌重新进入中文标题 | `exporting/dictionary_join.py` |

## Codex 自查闭环

1. 先读取 `codex_handoff.json`，以 `content_field_error_rate` 判断字段错误率是否达到门槛；需要了解受影响 SKU 范围时读取 `content_sku_error_rate`。不要用总体 `error_rate` 把发布阻断和内容错误混在一起。
2. 按 `likely_files` 和 `rule_counts` 聚合问题，优先处理 P0 的事实丢失、串字段和身份字段问题。
3. 只在源字段命中时做确定性修复；不能凭空补写源表没有的事实。
4. 用同一批次重新导出西语和中文，再次运行审计器。不同批次不能直接比较。
5. 内容错误率降到 10% 以下后，再处理 `release_block_rate` 对应的待审核状态、Gold 版本或人工批准流程。

## 当前参考问题清单

用户提供的审查文本指出的重点已映射到上述规则：分类映射错误、规格数字和单位损坏、描述/详情事实丢失或串字段、FSC/BCI 等官方认证丢失、来源异常未标注、标题品牌和技术事实变化、空源字段被生成内容、文本乱码以及待审核状态泄漏。审计器把这些问题拆成可定位的 SKU 级记录，供 Codex 修复后回归验证。
