# Action SKU Tracker 路线图

更新日期：2026-08-27

统一开发计划见 [`docs/MASTER_DEVELOPMENT_PLAN.md`](MASTER_DEVELOPMENT_PLAN.md)。该计划把
Export Foundation V1 正式发布和 SQLite Production Source of Truth 接管纳入同一总目标，
但仍按阶段门禁推进。

## 1. 总原则

- 不重写已经稳定的采集、Presence、生命周期和 QA 主链；
- 新字典和 Export 通过独立模块接入；
- 先冻结数据契约，再实现，再 dry-run，最后正式发布；
- SQLite 和图片下载主链继续冻结；
- 每一步都区分“本地已实现”“已提交”“已发布”。

## 2. 已完成阶段

### STEP 0：冻结稳定主链

- 已确认采集、生命周期和 QA 边界；
- 未重写 monitor/lifecycle 核心判断；
- 建立功能分支；
- 完整测试基线已保留。

### STEP 1：定义 Export Profile

- 定义字段所有权、正式来源、14 列结构和 manifest；
- 当前需求已升级为 Template 1 三表工作簿；
- 文档契约已冻结，机器配置仍待同步升级。

### STEP 2：ES 无图最小导出

- 本地工作区已实现；
- 只读取正式来源；
- 包含 SKU、价格、URL、来源和只读校验；
- 尚待单独代码提交和远端发布。

### STEP 3：ZH 无图字典 Join

- 本地工作区已实现；
- 字段级优先级、source hash 和 fallback 已覆盖；
- 与 ES SKU 集合对账；
- 尚待单独代码提交和远端发布。

### STEP 4：新 SKU 增量标准化

- 本地实现 `dictionary-enrich --run-id`；
- 只处理 NEW、source hash changed、NEEDS_REVIEW；
- 不访问官网、不调用模型、不全量重翻；
- 尚待单独代码提交。

### STEP 5：统一 Review Queue

- 本地实现 build/decide、稳定 review_id 和状态闭环；
- 已定义品牌、名称、类目、术语、源损坏和冲突类型；
- 尚待单独代码提交。

### STEP 6：术语候选成长管线

- 本地实现增量候选提取；
- 候选不自动晋升；
- 人工批准后才进入正式术语字典；
- 尚待单独代码提交。

## 3. 当前阶段：先发布基础 Export

虽然 ES/ZH 两个独立无图导出已经在本地工作区实现，但它们尚未完成独立提交、真实
正式来源预览和用户验收。当前优先级是先把基础导出变成可日常使用的稳定功能：

1. 隔离并审查现有 exporting 代码；
2. 冻结两个无图基础 Profile；
3. 完成正式来源、ES/ZH 对账、manifest 和只读校验；
4. 使用真实正式 run 生成预览；
5. 完整测试并独立提交；
6. 用户确认基础导出可用。

详细计划见 `EXPORT_IMPLEMENTATION_PLAN.md`。基础版本验收前不开始图片和三表合一。

## 3.1 Export V1 之后的 SQLite 接管计划

SQLite 生产接管已经纳入统一开发计划，执行顺序为：

| 阶段 | 目标 | 当前状态 |
|---|---|---|
| Phase 2 | Production Contracts、Writer Inventory、Schema V2 契约 | 待开发 |
| Phase 3 | Schema V2、CommitBundle、事务 Writer | 待开发 |
| Phase 4 | `SQLITE_SHADOW`，连续 3 次真实 parity | 待开发 |
| Phase 5 | DB Read Path，Excel 暂时继续写 | 待开发 |
| Phase 6 | Cutover Candidate、备份、回滚和重建验证 | 待开发 |
| Phase 7 | `SQLITE_PRIMARY` 正式接管 | 待开发 |
| Phase 8 | Excel/CSV 降级为 Generated View | 待开发 |
| Phase 9 | Image Contracts、AssetRecord、目录和状态冻结 | 待开发 |
| Phase 10 | Image Foundation：下载、标准化、QA、缓存、Derivative | 待开发 |
| Phase 11 | 20–50 SKU 真实图片切片 | 待开发 |
| Phase 12 | Full CURRENT 增量图片同步和性能基线 | 待开发 |
| Phase 13 | ES/ZH 带图 Export、Template 1 中文嵌图 | 待开发 |

因此当前“SQLite 冻结”只表示**尚未接入生产主链**，不是取消数据库接管计划。详细设计见
[`docs/MASTER_DEVELOPMENT_PLAN.md`](MASTER_DEVELOPMENT_PLAN.md)。

图片也已纳入同一总计划，但当前仍未开发；其基础能力可与 SQLite Contracts/Writer 并行，
带图 Export 必须等 P0 字段契约冻结，`image_assets` 元数据接入必须等 SQLite PRIMARY 稳定。

## 4. 后续阶段：STEP 7 + Template 1

### 4.1 历史 Presence 服务（已实现独立导出）

已实现基础服务与独立 `export-history` CLI；后续只需做真实来源验收：

1. 读取 `config/history_sources.yaml`；
2. 校验每个来源文件、工作表和 SKU 列；
3. 每批次按 SKU 去重；
4. 建立长期 SKU union；
5. 按来源能力写日期 `1/0/UNKNOWN`；
6. 保存原始行数、唯一 SKU 数、重复数和来源 hash；
7. 为 Template 1 和独立 `export-history` 提供同一服务。

### 4.2 Template 1

需要实现一个工作簿三张表：

- 商品上下架明细；
- 今日西班牙语清单；
- 今日中文清单。

重点：

- 当日日期列 `1` 的数量等于 CURRENT_VALID；
- 新 SKU 加入 union，历史为 0、当日为 1；
- ES/ZH SKU 集合和事实字段一致；
- 只有中文表嵌入本地 250×250 白底图片；
- 缺中文、详情或图片不能删除 SKU；
- 更新 `config/export_profiles.yaml`，使机器配置与文档一致。

## 5. STEP 8：整合、回归与上线

### 5.0 轻量 CI（已提交，首轮远端验证待完成）

- `.github/workflows/ci.yml` 在 push/PR 时按 `tests/ci_safe_tests.txt` 白名单运行 Python 3.12 的 CI-safe 测试；
- 依赖固定在 `requirements.txt` 与 `requirements-dev.txt`；
- CI 不访问官网、不安装浏览器、不写 Master/State/Dictionary、不下载图片、不发布基线；
- 当前本地工作区为 `224 passed`；CI 绿灯仍不能替代真实 daily-run 和 QA。

### 5.0.1 字典收口（本地已实现，正式回填未启用）

- `dictionary-coverage` 输出真实 AI-Free Coverage 和逐 SKU CSV；
- `dictionary_resolver` 输出字段级来源/状态及 `AUTO_READY/REVIEW_REQUIRED/SOURCE_BLOCKED`；
- `dictionary-apply --dry-run` 输出字段 diff、审核清单和 hash manifest；`--commit` 已实现独立 Apply Gate，
  但生产配置保持关闭；
- R2 高风险收口：未知品牌/未知源质量 fail-closed、布尔配置严格解析、基线/运行时逐文件 hash 绑定，
  并为正式 Apply 补齐唯一备份、替换后验证与可审计回滚；
- `review_closure_report.csv` 和 `source_blocked_review.csv` 分离记录可自动核验与必须人工/可信西语证据的项目；
- Review Queue 和 Term Candidates 已形成稳定去重、人工批准、正确知识层路由的闭环；
- 真实 run `2026-08-26_130145`：5,491 CURRENT、5,413 AUTO_READY（98.5795%）、71 REVIEW、
  7 SOURCE_BLOCKED；这些是本次运行报告，不是永久业务目标。

### 5.1 文档

- README、AGENTS、CURRENT_STATE、ARCHITECTURE、DATA_MODEL、QA_RULES、ROADMAP；
- 字典、生命周期和 Export 专题文档；
- 文档不得把本地未提交功能写成远端已发布功能。

### 5.2 CLI

目标入口：

```text
export-template --template action_full_template_1 --date YYYY-MM-DD [--run-id]
export-history [...]
dictionary-enrich --run-id ...
review-queue build/decide
term-candidates --run-id ...
```

最终命令名在实现时冻结；旧的 `export --lang es|zh --no-images` 可保留兼容期，但不能与 Template 1 状态混淆。

### 5.3 必须测试

- Export Profile 工作表和字段；
- 同日期幂等；
- ES/ZH SKU 集合一致；
- 人工覆盖优先级；
- source hash 失效；
- Review Queue 去重和闭环；
- 术语候选不得自动晋升；
- 历史 Presence 正确性和重复行对账；
- Sitemap-only 排除；
- QA FAIL 禁止正式 Export；
- 仅中文表插图和缺图不丢 SKU；
- 导出来源保持只读。

### 5.4 上线顺序

1. 使用历史文件执行只读 dry-run；
2. 生成 Template 1 preview；
3. 对账三张表、manifest 和图片统计；
4. 执行一次真实完整 daily dry-run；
5. QA PASS 后执行正式 observation；
6. 再次生成 export preview；
7. 人工确认后才标记正式可用并提交代码/配置/测试。

## 6. 发布前需要先处理的版本工作

当前本地工作区包含多个阶段的未提交代码。下一步应按功能拆分审查和提交：

1. Export ES/ZH 无图基础；
2. Dictionary Enrichment；
3. Review Queue；
4. Term Candidates；
5. 历史 Presence；
6. Template 1；
7. 字典基线数据更新。

每组提交必须只含对应代码、配置、测试和文档，不使用 `git add .`。

## 7. 暂缓事项

### SQLite

当前阶段继续冻结生产写入。Export V1 发布后按统一计划进入 Phase 2–8，经过
`SQLITE_SHADOW → DB Read Path → SQLITE_PRIMARY`，不直接硬切。当前正式 daily-run 仍以
Excel/CSV 为主链。

### 图片下载

下载和处理保持独立任务。Template 1 只读取本地图片；不因为导出自动启动下载。

### 全量翻译

不启用每日全量模型翻译。只允许增量任务、缓存和人工 Review 闭环。

## 8. Definition of Done

Template 1 和字典闭环只有同时满足以下条件才算完成：

- 代码、配置、文档一致；
- 全部测试通过；
- 历史 Presence 对账通过；
- 三张表 SKU 和数量关系通过；
- 中文和图片缺失不丢 SKU；
- manifest 可追溯到唯一正式 run；
- dry-run 和真实 preview 均通过；
- 只提交应进入 Git 的文件；
- 用户确认输出模板符合实际交付需求。
