# 统一 Review Queue（STEP 5）

> 当前状态：本地功能分支已实现并通过测试，代码尚待单独审查、提交和远端发布。

运行时队列位于 `runtime/review_queue/review_queue.csv`。它是审核状态的唯一
保存位置；Master 的 `06_REVIEW_QUEUE` 仍是生命周期审计证据，只读导入，绝不
由本模块回写。

## 数据契约

每行都有：`review_id、issue_type、sku、field、current_value、suggested_value、
evidence、reason、created_at、status`，并额外记录来源、更新时间和人工决议。
`review_id` 由问题类型、SKU、字段、当前值、建议值和证据稳定计算；相同未解决
问题重复构建时只会保留一条，不会每天新增副本。

支持生命周期问题及字典问题：`BRAND_CANDIDATE`、`TERM_CANDIDATE`、
`NAME_REVIEW`、`CATEGORY_REVIEW`、`SOURCE_HASH_CHANGED`、
`MODEL_LOW_CONFIDENCE`、`SOURCE_DAMAGED`、`SOURCE_POLLUTED`、
`DICTIONARY_CONFLICT`。状态只能是 `PENDING / APPROVED / REJECTED / RESOLVED`。

## 命令

```powershell
$env:PYTHONPATH = "src"
python -m action_tracker review-queue build --run-id 2026-08-25_025953
python -m action_tracker review-queue decide --review-id <id> --decision APPROVED --value "人工确认值"
```

构建会只读汇集：Master `06_REVIEW_QUEUE`、`source_damage_report.csv`、旧品牌
候选和指定 run 的增量审核证据。人工批准后按问题类型写入正确的字典位置：

| 类型 | 写入位置 |
| --- | --- |
| `NAME_REVIEW`、`CATEGORY_REVIEW` | `manual_overrides.csv`（字段级） |
| `BRAND_CANDIDATE` | `brand_dictionary.csv` + SKU 的 `manual_overrides.csv` |
| `TERM_CANDIDATE` | `term_dictionary.csv` |

批准后执行一次字典重建，再运行 `review-queue build`。若源问题已被解决，队列
会自动转为 `RESOLVED`（保留审计行、从待办中消失），也不会重新生成同一问题；
拒绝项只保留 `REJECTED` 审计状态。`SOURCE_DAMAGED` 等
官网事实问题不会被人工中文覆盖，必须等待可信西语证据恢复后才会自然消失。

Review Queue 的稳定 ID、去重和状态迁移在 CI 中使用临时队列验证；CI 不读取或修改本机正式审核队列。
