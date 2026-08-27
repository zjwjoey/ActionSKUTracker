# Action SKU Tracker 当前状态

更新日期：2026-08-27
项目目录：`F:\ActionSKUTracker`
当前分支：`feat/export-dictionary-closure`

## 1. 生产主链边界

Sitemap/Listing/补充入口 → Presence 冻结 → Lifecycle → QA → Snapshot/Staging →
QA 通过且非 dry-run 才能写入 Master/State。SQLite 仍冻结，不参与生产主链。本轮没有修改
`monitor/listing.py`、`monitor/sitemap.py`、`monitor/sku_monitor.py`、`services/lifecycle.py`
或 Presence/Cloudflare/QA 核心语义。

## 2. 字典真实基线

| 数据集 | 当前正式/运行时行数 |
| --- | ---: |
| 商品字典 | 8,617 |
| 品牌字典 | 588（工作区候选；已发布基线 509） |
| 类目关系 | 185 |
| 术语字典 | 44（工作区；已发布基线 33） |
| 人工覆盖 | 30 |
| 模型缓存 | 825（811 OK、14 NEEDS_REVIEW） |
| SOURCE_DAMAGED/SOURCE_POLLUTED | 131 |

运行时字典若缺少通过审计且与基线 hash 一致的证据，会自动回退到
`data/dictionary/`，不会把临时未审数据当成正式导出来源。

## 3. 真实覆盖率验收

针对正式 run `2026-08-26_130145` 的 CURRENT：

| 指标 | 数值 |
| --- | ---: |
| CURRENT SKU | 5,491 |
| AUTO_READY | 5,399 |
| AI-Free Rate | 98.3245% |
| REVIEW_REQUIRED | 84 |
| SOURCE_BLOCKED | 8 |
| 未确认品牌 | 3 |
| source_hash 变化 | 81 |
| SOURCE_DAMAGED / POLLUTED | 1 / 7 |
| 模型缓存使用 | 1（依赖率 0.0182%） |

稳定目标 98% 已超过约 18 个 SKU（0.3245 个百分点），但 84 条审核和 8 条源阻断不能
被忽略。报告在 `runtime/dictionary/reports/dictionary_coverage_2026-08-26.{json,csv}`。

## 4. 已实现的字典闭环

- `dictionary_resolver.py`：按字段输出值、来源、状态和 SKU 级
  `AUTO_READY/REVIEW_REQUIRED/SOURCE_BLOCKED`；人工覆盖优先，模型缓存仅在 source_hash
  匹配且质量为 OK 时可用；普通西语残留会进入审核。
- `dictionary-coverage`：只读统计 CURRENT，不修改 Master、State 或字典。
- `dictionary-apply --dry-run`：生成 `apply_preview.csv`、`review_required.csv`、
  `apply_manifest.json`；真实 run 预览为 AUTO_READY 5,399、84 条审核、8 条阻断。
- `dictionary-enrich`：只选择 NEW、source_hash 变化和 NEEDS_REVIEW，不访问官网、不调用模型。
- `review-queue build/decide`：稳定 review_id 去重，批准后按问题类型写入正确知识层；
  拒绝保留审计状态，已解决问题转 RESOLVED。
- `term-candidates`：从增量 SKU 统计术语、频次、覆盖 SKU、类目分布、上下文和来源日期；
  候选永不自动晋升，只有人工 APPROVED 才写入正式术语字典。

Dictionary Apply 的正式 Master 写入当前仍明确关闭；当前阶段只允许 dry-run，防止未审
字段进入生产主表。Master 修改前后 hash、字段 diff 和原子替换 Gate 将作为后续单独启用项。

## 5. Export 状态

基础 ES/ZH 无图导出和 Template 1 三表无图导出已在本地实现。Template 1 示例输出包含：
历史 Presence union、当期 0/1 日期列、今日西语表、今日中文表；中文图片嵌入仍未启用，
图片下载模块继续独立冻结。Export 只读取正式 QA/FULL_COMMIT 来源，不重新访问官网。

## 6. 测试与 CI

当前完整回归：`209 passed`。新增 Resolver、Coverage、Apply、Review Queue、Term Candidate、
Export 和 Template 1 测试已加入 CI-safe 白名单；CI 仅使用临时 fixture，不访问官网、不写生产
runtime、不发布字典基线。GitHub Actions 远端结果仍需以实际 workflow run 为准。

## 7. 未完成/风险

1. Dictionary Apply 正式写 Master 尚未启用；必须补齐 QA/FULL_COMMIT/Audit Gate、字段 diff、
   备份和原子替换后才能开放。
2. 84 条 Review Required 和 8 条 Source Blocked 需要人工/可信西语证据处理。
3. 术语 scope、图片下载与带图 Export、export-history 独立功能尚未进入生产主链。
4. 工作区仍有此前 Template 1 与字典功能的待提交改动，提交时必须按功能拆分，不能混入
   runtime、报告、图片或密钥。
