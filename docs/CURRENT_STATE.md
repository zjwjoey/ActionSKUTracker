# Action SKU Tracker 当前状态

更新日期：2026-08-27
项目目录：`F:\ActionSKUTracker`
当前分支：`feat/export-foundation-v1`

## 1. 生产主链边界

Sitemap/Listing/补充入口 → Presence 冻结 → Lifecycle → QA → Snapshot/Staging →
QA 通过且非 dry-run 才能写入 Master/State。SQLite 当前仍冻结、不参与生产主链，但已纳入
Export V1 之后的 SQLite PRIMARY 接管计划。本轮没有修改
`monitor/listing.py`、`monitor/sitemap.py`、`monitor/sku_monitor.py`、`services/lifecycle.py`
或 Presence/Cloudflare/QA 核心语义。

## 2. 字典真实基线

| 数据集 | 当前正式/运行时行数 |
| --- | ---: |
| 商品字典 | 8,662 |
| 品牌字典 | 588（当前功能分支基线；main 旧基线 509） |
| 类目关系 | 186 |
| 术语字典 | 44（当前功能分支基线；main 旧基线 33） |
| 人工覆盖 | 197 |
| 模型缓存 | 823 |
| SOURCE_DAMAGED/SOURCE_POLLUTED | 130 |

运行时字典若缺少通过审计且与基线 hash 一致的证据，会自动回退到
`data/dictionary/`，不会把临时未审数据当成正式导出来源。

## 3. 真实覆盖率验收

针对正式 run `2026-08-26_130145` 的 CURRENT：

| 指标 | 数值 |
| --- | ---: |
| CURRENT SKU | 5,491 |
| AUTO_READY | 5,413 |
| AI-Free Rate | 98.5795% |
| REVIEW_REQUIRED | 71 |
| SOURCE_BLOCKED | 7 |
| 未确认品牌 | 3 |
| source_hash 变化 | 11 |
| SOURCE_DAMAGED / POLLUTED | 0 / 7 |
| 模型缓存使用 | 0（依赖率 0%） |

稳定目标 98% 已超过约 32 个 SKU（0.5795 个百分点），但 71 个审核 SKU 和 7 个源阻断不能
被忽略。报告在 `runtime/dictionary/reports/dictionary_coverage_2026-08-26.{json,csv}`。

## 4. 已实现的字典闭环

- `dictionary_resolver.py`：按字段输出值、来源、状态和 SKU 级
  `AUTO_READY/REVIEW_REQUIRED/SOURCE_BLOCKED`；人工覆盖优先，模型缓存仅在 source_hash
  匹配且质量为 OK 时可用；普通西语残留会进入审核。
- `dictionary-coverage`：只读统计 CURRENT，不修改 Master、State 或字典。
- `dictionary-apply --dry-run`：生成 `apply_preview.csv`、`field_diff.csv`、`review_required.csv`、
  `apply_manifest.json`；2026-08-26 真实 run 在严格未知品牌门禁后为 AUTO_READY 5,410、74 个审核 SKU、7 个源阻断。
- `dictionary-enrich`：只选择 NEW、source_hash 变化和 NEEDS_REVIEW，不访问官网、不调用模型。
- `review-queue build/decide`：稳定 review_id 去重，批准后按问题类型写入正确知识层；
  拒绝保留审计状态，已解决问题转 RESOLVED。
- `term-candidates`：从增量 SKU 统计术语、频次、覆盖 SKU、类目分布、上下文和来源日期；
  候选永不自动晋升，只有人工 APPROVED 才写入正式术语字典。

Dictionary Apply Gate 已实现，正式 Master 写入仍由 YAML 布尔值
`dictionary_apply.production_enabled=false` 明确关闭。Gate 会校验 QA/FULL_COMMIT、未过期审计、
Resolver、CURRENT 集合、运行时字典/基线逐文件 hash、并发 hash、字段白名单、唯一备份/锁、暂存验证、
原子替换和替换后回读；任何后续校验异常均恢复备份并记录 manifest 状态。当前 full dry-run 的不可变事实变化为 0。

## 5. Export 状态

基础 ES/ZH 无图导出、Template 1 三表无图导出和独立历史 Presence 导出已在本地实现。
历史 Presence 使用 `1/0/UNKNOWN` 三态并附历史来源审计；中文图片嵌入仍未启用，
图片下载模块继续独立冻结。Export 只读取正式 QA/FULL_COMMIT 来源，不重新访问官网。

## 6. 测试与 CI

当前完整回归：`240 passed`。新增 Resolver、Coverage、Apply、Review Queue、Term Candidate、
Export 和 Template 1 测试已加入 CI-safe 白名单；CI 仅使用临时 fixture，不访问官网、不写生产
runtime、不发布字典基线。GitHub Actions 远端结果仍需以实际 workflow run 为准。

## 7. 未完成/风险

1. Dictionary Apply 正式写 Master 尚未启用；Gate 已完整实现，但生产配置仍关闭。
2. 74 个 Review Required 和 7 个 Source Blocked 需要人工/可信西语证据处理；已分别生成
   `review_closure_report.csv` 与 `source_blocked_review.csv`，不使用中文反推西语。
3. 术语 scope、图片下载与带图 Export 尚未进入生产主链；历史 export-history 已实现，待正式发布验收。
4. 工作区仍有此前 Template 1 与字典功能的待提交改动，提交时必须按功能拆分，不能混入
   runtime、报告、图片或密钥。

SQLite Production Source of Truth 的完整阶段计划（Contracts → Writer → Shadow → Read →
Cutover → PRIMARY）见 `docs/MASTER_DEVELOPMENT_PLAN.md`，目前尚未开始生产接管实现。
