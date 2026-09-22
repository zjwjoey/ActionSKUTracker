# Action SKU Tracker 当前状态

更新日期：2026-09-22
项目目录：仓库根目录
当前分支：`fix/naming-history-export-20260917`

当前生产安全收口提交为 `3826be0`，长期工具隔离提交为 `3ec134a`。
本文其余 Stage 4/5/6 数量是历史运行快照，不代表当前字典已获得新的 Owner/官网证据批准。

## 2026-09-12 Stage 4 Closure 最新状态

本节覆盖后文旧的 Stage 4/Qwen 快照；后文历史数量和状态仅保留作当时记录。

- Owner 签字包：`Qwen_Stage4_Stage5_OWNER_SIGNED_20260912.xlsx`，Stage 4 13 条 Gold 已确认。
- 2026-09-12 本次对话已明确确认两份 AI Owner Ready 包，登记为 owner-confirmed package（不改写原始工作簿）：Silver 485/485 覆盖完成，其中 472 条直接接受、12 条修订后接受、1 条 `3223907` 源冲突隔离；P0/P1 包 10 条 P0 + 1 条 P1 均已确认其根因处置。
- 源冲突 `3006792`、`3224748`：已按 owner 决定隔离，不进入 Gold。
- 旧 `NUMERIC_DROPPED`：已在当前 evaluator/acceptance 口径中降为历史信号；当前 Closure 的源数字丢失数为 0。
- 冻结 adapter 的 485 条独立测试已完成：JSON/schema/非空/一级类目均通过，但字段级硬事实仍有 10 条 P0、1 条 P1。
- 当前 recovery state：`READY_FOR_STAGE5_OFFLINE_SHADOW`；冻结 485 条在源绑定修复层复核后 P0/P1 均为 0，`FULL_STAGE4_RELEASE=true`。当前放行模式为 `MODEL_PLUS_SOURCE_BOUND_OWNER_RESOLVER`；训练授权和生产写入仍保持 `false`。
- 完整报告：`docs/STAGE_4_OWNER_SIGNED_FULL_EVAL_RECHECK_20260912.md`；人工审核包：
  `runtime/training/qwen3_8b/20260911/stage4_full_eval_owner_signed_p0_review_package_20260912.csv`。
- 独立源字段核验确认这 10 条 P0 均有直接西语源字段证据（不是单纯计数器表面误报）。
- 最终 Closure 总报告：`docs/STAGE_4_FINAL_CLOSURE_REPORT_20260912.md`；failure certification：
  `runtime/training/qwen3_8b/20260911/stage4_final_failure_certification.csv/json`。
- 初始 50 条 targeted remediation 队列保留为历史证据；v4 初选 200 条后，规则拦截 48 条。为满足“最终 200 条可审核候选”的目标，v5 补充池再审校并按五类各 40 条重组最终包，与既有冻结测试、训练/验证/测试和硬测试集交叉为 0：
  `runtime/training/qwen3_8b/20260911/stage4_targeted_remediation_review_queue_v4_200.csv/json`。
- 最终人工审核包为 200 条模型审校通过候选（品牌、数字遗漏、数字幻觉、技术 token、产品对象各 40 条），另保留两批原始规则拦截记录和 35 条合格备用：
  `runtime/training/qwen3_8b/20260911/stage4_remediation200_final_owner_review_20260912.xlsx`。
- 这 200 条已与当前 Master 的西语六字段逐条匹配，均为 candidate-only，`training_eligible=0`。受控校验入口 `scripts/validate_stage4_remediation_owner_review.py` 已切换到最终 200 条；后续仅签字完整、源字段未变化且 Guard 通过的行可输出为 Gold，脚本本身不训练、不写生产数据。
- 外部 AI 审核表标记为 `ACCEPT_AS_GOLD` 的 177 条已由项目所有者确认并登记；泄漏核验发现它们全部已出现在旧 train/validation/test 语料（131 train、29 validation、17 field-test），因此被标记为 `HUMAN_CONFIRMED_REMEDIATION_GOLD_LEAKAGE_BLOCKED`，`training_eligible_rows=0`，不得直接训练。原始候选和确认结果均保留，未写入 Master 或既有 split。
- 已修正 `scripts/build_stage4_targeted_remediation_queue.py`：生产模式现在扫描整个 `runtime/training/qwen3_8b` 历史根目录，统一排除所有 train/validation/test、冻结测试和既有候选语料；新增回归测试覆盖跨日期目录泄漏。
- 新收到的 AI Owner Ready 包已核验并经项目所有者在本次对话确认；原始工作簿中的签字列保持不变，确认记录见：
  `runtime/training/qwen3_8b/20260911/stage4_owner_confirmed_closure_20260912.json`。
- AI Owner Ready 复核报告：`runtime/training/qwen3_8b/20260911/stage4_ai_owner_ready_recheck_20260912.json`。
- 历史 Stage 4 P0 阻断已通过源绑定、字段级、Owner 已批准的修复层闭环；旧候选与严格审计文件仍保留为不可变历史证据，不覆盖旧模型产物。
- 本轮 Closure 只读审计未修改 Master、SQLite、字典或旧模型；最新回归测试为 `437 passed`。

## 2026-09-08 最新生产验收覆盖

以下是当前 PRIMARY 的最新状态，覆盖本文件后面的历史快照；后面的旧数量只作为当时验收记录：

- SQLite PRIMARY：`runtime/db/action_tracker.db`
- products：9,033；CURRENT：5,547；MISSING：26；OFFLINE：23；HISTORICAL：2,610；ABSENT：827
- 最新 committed head：`LOCALIZATION_PROVENANCE_REPAIR_20260908_localization_LOCALIZATION_PROVENANCE_REPAIR_20260908_bc9c848a_b35ca62aa1ef`
- 最新 Apply run：QA `PASS`、`dry_run=0`，5,547 个 CURRENT SKU 已写入字段级本地化和 canonical provenance
- SQLite integrity、foreign keys、presence states：`PASS`
- Master/known_skus/offline_skus 兼容投影：`export_sync=SUCCESS`
- 2026-09-08 中文/西语无图导出：各 5,547 条，SKU、价格、图片链接、商品链接逐条一致
- 当前分支的生产代码仍未合并 main，需按最小逻辑进行集成审查

当前保留两类非阻断告警：部分官网详情/二级类目源字段本身为空（导出备注已显式标记），
以及 51 个历史 SKU 没有可追溯的 `source_first_seen`、部分字典条目仍处于人工复核队列。
字典审计最新结果为 30 PASS / 2 WARN / 0 FAIL；西语导出 HTML、`null/undefined` 和双冒号残留均为 0。

## 1. 生产主链边界

Sitemap/Listing/补充入口 → Presence 冻结 → Lifecycle → QA → Snapshot/Staging →
QA 通过且非 dry-run 才能提交 SQLite PRIMARY，再生成兼容 Master/State。当前
`storage.mode=SQLITE_PRIMARY`，SQLite 是生产主链，Excel/CSV 是兼容投影。本轮没有修改
`monitor/listing.py`、`monitor/sitemap.py`、`monitor/sku_monitor.py`、`services/lifecycle.py`
或 Presence/Cloudflare/QA 核心语义。

详情运行策略：`run.max_detail_per_run=20` 是生产默认上限；值 `0` 只表示本轮禁用详情导航，
超额候选进入 backlog，绝不表示无限抓取。详情页遇到 Cloudflare challenge 时最多等待 5 分钟，在第
2、4 分钟各刷新一次，仍未恢复则写入 `DETAIL_CHALLENGE_TIMEOUT` 并停止详情阶段；不绕过
验证，也不影响已经冻结的 Presence 事实。

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
  失败隔离和 SQLite PRIMARY 元数据镜像；当前配置不自动下载图片，Manifest 为空属于当前运行状态。

## 7. 测试与 CI

当前完整回归：`283 passed`。新增 Resolver、Coverage、Apply、Review Queue、Term Candidate、
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
4. 工作区仍有此前 Template 1 与字典功能的待提交改动，提交时必须按功能拆分，不能混入
   runtime、报告、图片或密钥。

SQLite Production Source of Truth 的完整阶段计划（Contracts → Writer → Shadow → Read →
Cutover → PRIMARY）见 `docs/MASTER_DEVELOPMENT_PLAN.md`；当前完成了 Writer、接线、Read
Repository、三轮真实 Shadow 对账以及隔离副本的迁移/备份/恢复/Primary 演练，下一步是正式切换窗口评审。
Image Foundation 的 Phase 9–13（Contracts → Foundation → Slice → Full Sync → With-Images Export）
已完成 Contracts、Foundation、ES/ZH With-Images Export 和 Template 1 中文嵌图实现，真实全量同步/性能基线待执行。
