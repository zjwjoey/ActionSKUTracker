# Edge 详情补录模块

当正式详情会话进入 `BLOCKED`、但 Edge 浏览器低频打开商品页成功时，可以把 Edge 读取到的**已验证西语详情**保存为 UTF-8 JSON，再导入项目。

该模块不是绕过 Cloudflare 的工具。它只接受已经通过页面验证的结果，拒绝挑战页、乱码、错误 SKU、错误 Action 链接和非当前 SKU。导入不会改变在售状态、生命周期、价格、促销、图片状态或中文字段。

## 输入格式

```json
{
  "source": "EDGE_PLUGIN",
  "parent_run_id": "2026-09-03_072729",
  "records": [
    {
      "sku": "2571395",
      "page_status": "OK",
      "page_title": "Iluminación navideña multicolor – 3.6W, 20 m | Action ES",
      "product_url": "https://www.action.com/es-es/p/2571395/iluminacion-navidena-multicolor/",
      "name_es": "Iluminación navideña multicolor",
      "cat1_es": "Vivienda",
      "cat2_es": "Decoración",
      "spec_es": "240 luces LED | 20 metros",
      "desc_es": "...",
      "details_es": "Color: Multicolor; ...",
      "image_url": "https://asset.action.com/...",
      "observed_at": "2026-09-03T15:00:00+08:00"
    }
  ]
}
```

`desc_es` 和 `details_es` 必须同时有值才能进入 PRIMARY。`spec_es`、类目和名称属于 Listing 事实，Edge 只能作为证据，不能覆盖它们。所有文本必须是 UTF-8，不能含替换字符 `�` 或控制字符。

## Master 对齐 staging 表

需要先保留在独立表格时，使用 `detail-edge-export`。它可读取 QA PASS 的正式提交或 dry-run 父 run（仅暂存，不写入）；生成的唯一商品工作表与 `02_SKU_ES_CURRENT` **30 列同名、同顺序**；Edge 只替换描述和产品详情，其余字段从当前 Master 和生命周期状态补齐。详情中的制表符/换行字段会标准化为 Master 使用的 `字段; 值` 形式。

```powershell
python -m action_tracker detail-edge-export `
  --run-id 2026-09-03_053952 `
  --input .\edge_details_batch.json `
  --output .\Edge_Detail_Staging.xlsx
```

staging 表上的空值不会用猜测或占位事实填充；命令结果会返回 `missing_fields_by_sku` 和 `rows_ready_for_master`。存在缺失的行只能审核，不能写入 Master。Cloudflare、乱码或异常页面只保留在原始 JSON 证据中，不进入商品工作表。

命令会先读取 Master 的 `02_SKU_ES_CURRENT` 首行并与 30 列契约逐字校验；表头漂移会直接失败。结果还会返回 `rows_ready_for_detail_merge`，它只表示身份、价格、Listing 事实、日期和两项详情齐全，可供详情字段的窄写入流程复核；它不授权覆盖名称、类目、规格、价格或生命周期。`rows_ready_for_master` 更严格，要求 30 列全部有值。原价、历史变价、折扣标签等源数据没有证据时必须保持空值并进入审核，不能用当前价、空字符串或“未知”伪造。

## 命令

先预览，不写入：

```powershell
$env:PYTHONPATH='F:\ActionSKUTracker\src'
python -m action_tracker detail-edge-import --run-id 2026-09-03_072729 --input .\edge_details.json
```

确认预览无误后才写入：

```powershell
python -m action_tracker detail-edge-import --run-id 2026-09-03_072729 --input .\edge_details.json --commit
```

写入路径是 SQLite PRIMARY 的西语事实和详情字段，成功后自动重建兼容 Master。每次导入会在父 Snapshot 下保存原始输入、规范化记录和导入报告，便于追溯。

## 正式 run 延迟详情入库

对于父 run 已经 QA PASS、正式提交的延迟详情，使用专用的窄入口。新版本以父
Snapshot 下的 `detail_backlog.csv`（`queue_status=DEFERRED`）作为权威队列，
覆盖 `NEW`、`REAPPEARED`、`CATEGORY_MISSING` 等因本轮详情上限而延期的 SKU；
旧 run 没有该文件时，兼容回退到 `product_updates.csv` 中
`MISSING_FIELD + detail_selected=false` 的记录。入口只更新西语
`description` 与 `details` 两个详情字段：

```powershell
$env:PYTHONPATH='F:\ActionSKUTracker\src'
python -m action_tracker detail-deferred-import `
  --run-id 2026-09-08_035740 `
  --input .\deferred6_待入库.json
```

预览通过后追加 `--commit` 才会写入 SQLite PRIMARY 并重建兼容 Master。该入口
拒绝 dry-run、未提交父 run、不在权威延迟队列中的 SKU、已经存在任一详情字段的
SKU、挑战页和不完整详情，不会改动价格、生命周期、名称、类目、规格、链接、
图片或中文字段。已完整详情的 SKU 必须从证据批次中剔除，另走独立修正流程。

## 正式 run 延期类目回填

当延期证据中的官网面包屑可以补齐 Master 的空二级类目时，使用独立的类目入口，
不要把类目混入详情写入。该入口同样只接受 QA PASS、非 dry-run、FULL_COMMIT 的父
run，并以 `detail_backlog.csv` 的完整 `DEFERRED` 集合作为输入覆盖校验：

```powershell
$env:PYTHONPATH='F:\ActionSKUTracker\src'
python -m action_tracker category-deferred-reconcile `
  --run-id 2026-09-10_030315 `
  --input .\detail_edge_import_20260910_50.json
```

预览会列出可回填行和类目冲突。默认只有空类目或与证据完全一致的行可以进入提交计划；
已有非空一级/二级类目但与官网证据不一致的 SKU 会被隔离，带冲突的批次不能
`--commit`。若已通过浏览器逐条核验官网面包屑，可用重复的
`--approve-conflict-sku <SKU>` 逐 SKU 放行，未列出的冲突仍会阻断提交。该入口只回填西语官网面包屑、重新计算来源哈希和审计记录，不改变
价格、Presence、生命周期、详情或中文字段。类目冲突处理完成后，再执行
`detail-deferred-import` 写入真正缺失的描述和产品详情。
