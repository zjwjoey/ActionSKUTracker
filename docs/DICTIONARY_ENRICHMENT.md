# 增量字典标准化（STEP 4）

> 当前状态：本地功能分支已实现并通过测试，代码尚待单独审查、提交和远端发布。

`dictionary-enrich` 是正式 Observation 成功后的独立补充阶段，不接入
`daily.py`，不影响 Presence、生命周期或 Master 提交。

## 命令与前置门禁

```powershell
$env:PYTHONPATH = "src"
python -m action_tracker dictionary-enrich --run-id 2026-08-25_025953
```

命令只接受同时满足以下条件的 snapshot：`dry_run=false`、`FULL_COMMIT`、
QA `PASS`（或已定义的 `PASS_PRESENCE_ONLY`）。任一条件不满足即拒绝，且不会
写入字典。它只读取 snapshot 中已冻结的官网事实，绝不重新访问 Action 网站。

## 选择范围

只选择本轮仍在 snapshot 中的三类 SKU：

1. `sku_delta.csv` 中 `status=NEW`；
2. 官网事实哈希变化（西语品名、一级/二级类目、规格）；
3. 商品字典仍标记 `NEEDS_REVIEW`。

价格、在售状态、中文派生字段不参与哈希。Listing 中的空字段不等于官网删除：
它会保留既有已确认事实；已识别的网页 UI 污染则明确清空并隔离。未选中的历史 SKU 原样保留，
不会产生新翻译，也不会更新其 `updated_at`。

“入选”不等于“可写”。如果 `NEW` SKU 已有同源长期字典记录，或待审核 SKU
没有同源模型缓存，它只保留审核证据，绝不会被本轮 Listing 的 fallback 中文覆盖。

## 处理与边界

选中 SKU 依次套用字段级人工覆盖、商品字典、15 个一级类目映射、已有同源
模型缓存。此阶段**不调用模型、不调用第三方翻译服务，也不修改
`translation_enabled`**。无法确认的品牌不会从品名猜测；名称、规格、类目或
品牌疑点会写到 `runtime/dictionary/enrichment/<run_id>/review_candidates.csv`。

该 CSV 是 STEP 4 的运行证据；统一 Review Queue 会读取并去重这些候选，
不会与每日生命周期 Review 证据相互覆盖。

每次运行同时写入：

- `selected_skus.csv`：SKU、被选原因、官网事实哈希；
- `review_candidates.csv`：未能自动确认的问题；
- `enrichment_report.json`：输入 run、数量、字典变更和无网络声明。
