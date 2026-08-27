# 术语候选成长管线（STEP 6）

> 当前状态：本地功能分支已实现并通过测试；候选只进入审核队列，不自动入正式术语字典。

术语字典的增长必须由人工确认，不允许模型或规则直接向
`term_dictionary.csv` 批量写入。

## 输入与命令

```powershell
$env:PYTHONPATH = "src"
python -m action_tracker term-candidates --run-id 2026-08-25_025953
python -m action_tracker review-queue build --run-id 2026-08-25_025953
```

输入严格限于同一正式 `FULL_COMMIT + QA PASS` run 的 STEP 4 增量 SKU：
`NEW`、官网事实哈希变化、`NEEDS_REVIEW`。它不重扫长期 8,000 多 SKU，不访问官网，
不调用模型，也不会改动正式术语字典。

候选保存在 `runtime/dictionary/term_candidates/<run_id>/term_candidates.csv`，字段为：
`term_es、suggested_zh、term_type、occurrence_count、sku_count、cat1_distribution、
sample_contexts、source_dates、decision、review_status`。`suggested_zh` 初始为空，
上下文只用于人工判断，绝不声称是该词的自动译法。程序兼容旧候选包字段，但新输出使用
上述 canonical schema。

默认候选至少覆盖 2 个 SKU；需要抽样低频词时再显式使用 `--min-sku-count 1`。
已在正式术语字典中出现的词不会再次作为候选。

## 人工闭环

运行 `review-queue build` 后，候选成为 `TERM_CANDIDATE`。人工确认时：

```powershell
python -m action_tracker review-queue decide --review-id <id> --decision APPROVED --value "中文术语" --term-type material
```

批准才会写入 `term_dictionary.csv`；拒绝只记为 `REJECTED`。术语类型可用
`--term-type` 覆盖候选的保守默认值 `general`。当前正式术语表尚未引入类目 scope：
候选先保留类目分布证据，scope/category 扩展需单独做 schema 迁移，不能在本阶段
静默改变既有词的适用范围。

候选提取的回归测试可在 CI 中运行，但 CI 不从真实运行区提取候选，也不会自动晋升或写入正式术语字典。
