# Action SKU Tracker 当前状态

更新日期：2026-08-30
项目目录：`F:\ActionSKUTracker`
当前分支：`fix/p0-p1-final-closure`（基于 `origin/main=dd0fa61`，带入 `bcb8709` hotfix）

## 1. 生产主链边界

Sitemap/Listing/补充入口 → Presence 冻结 → Lifecycle → QA → Snapshot/Staging →
QA 通过且非 dry-run 才能提交 SQLite PRIMARY，再生成兼容 Master/State。当前
`storage.mode=SQLITE_PRIMARY`，SQLite 是生产主链，Excel/CSV 是兼容投影。本轮没有修改
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

基础 ES/ZH 无图导出、Template 1 三表无图/带图导出和独立历史 Presence 导出已在本地实现。
历史 Presence 使用 `1/0/UNKNOWN` 三态并附历史来源审计；图片资产同步、250×250 白底衍生图
和 ES/ZH 带图 Export 已实现，缺图不会删除 SKU。Export 只读取正式 QA/FULL_COMMIT 来源，
不重新访问官网。

## 6. SQLite 与图片实现状态

- SQLite V2：`CommitBundle`、`BEGIN IMMEDIATE`、外键/完整性检查、幂等 run、`base_commit_id`
  乐观门禁、`export_sync`、`sync-exports` 和 PRIMARY Read Repository 已实现并有 fixture 测试。
- 正式 runtime 数据库已完成 V2 baseline、parity 校验和 PRIMARY 切换；旧 V1 镜像与配置备份位于
  `runtime/backups/formal_cutover_20260830_120733`。三轮隔离 `SQLITE_SHADOW` 均为 parity 0，
  正式切换后的数据库校验也通过。
- 图片：`image-sync` 支持低并发、超时、指数退避、staging 原子 promotion、Manifest checkpoint、
  失败隔离和 SQLite PRIMARY 元数据镜像；当前配置不自动下载图片，真实全量同步与性能验收属于 P2。

## 7. 测试与 CI

当前完整回归：`293 passed`。新增 Resolver、Coverage、Apply、Review Queue、Term Candidate、
Export 和 Template 1 测试已加入 CI-safe 白名单；CI 仅使用临时 fixture，不访问官网、不写生产
runtime、不发布字典基线。GitHub Actions 远端结果仍需以实际 workflow run 为准。

## 7.1 Knowledge Production V1（P3–P6）

已完成合同与离线安全基础：统一六字段 source hash、字段级 Resolver、增量翻译队列去重、
SOURCE_BLOCKED 排除、候选 Validator、字段级 Auto-Approval Shadow，以及 SQLite 的
`translation_resolution`、`translation_queue`、`translation_candidates`、
`translation_approval_audit` 表和 localization provenance 字段。配置中的
`knowledge.production_apply_enabled`、`translation.ai_enabled`、
`translation.auto_approval_enabled` 和 `scoped_dictionary.enabled` 均保持关闭。
生产 Apply、真实 AI provider、Scoped Dictionary 审批和 Auto-Approval 正式开启尚未执行。

## 8. 未完成/风险

1. Dictionary Apply 正式写 Master 尚未启用；Gate 已完整实现，但生产配置仍关闭。
2. 74 个 Review Required 和 7 个 Source Blocked 需要人工/可信西语证据处理；已分别生成
   `review_closure_report.csv` 与 `source_blocked_review.csv`，不使用中文反推西语。
3. 图片尚未进入自动 daily 主链；需要基于正式 CURRENT 单独运行 `image-sync`，再运行带图 Export。
   当前已完成 fixture/结构验收，真实全量图片性能基线仍待执行。
4. P0/P1 已冻结；后续只接受 BUG、SECURITY 或 DATA INTEGRITY 修复，不再进行架构重写。

SQLite Production Source of Truth 的完整阶段计划（Contracts → Writer → Shadow → Read →
Cutover → PRIMARY）见 `docs/MASTER_DEVELOPMENT_PLAN.md`；当前完成了 Writer、接线、Read
Repository、三轮真实 Shadow 对账以及隔离副本的迁移/备份/恢复/Primary 演练，下一步是正式切换窗口评审。
Image Foundation 的 Phase 9–13（Contracts → Foundation → Slice → Full Sync → With-Images Export）
已完成 Contracts、Foundation、ES/ZH With-Images Export 和 Template 1 中文嵌图实现，真实全量同步/性能基线待执行。
