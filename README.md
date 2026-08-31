# Action SKU Tracker

Action 西班牙站商品每日监测、生命周期管理、中文标准化和 Excel 导出项目。

项目以 Action ES 官网西班牙语内容为事实，以 Listing/Sitemap Presence 判断商品是否被观察到，以本地 Excel、CSV 和 JSON 保存可审计证据。中文名称、品牌、类目和术语属于派生数据，不能反向改写官网事实。

## 当前能力

- Sitemap、15 个主类目、Nuevo 和 Promoción semanal 入口采集；
- NEW、ACTIVE、MISSING、OFFLINE、REAPPEARED 生命周期；
- Presence 在 Detail 前冻结，详情访问受限不会把商品误判下架；
- QA 门禁、Snapshot、Staging 和原子更新 Master；
- 商品、品牌、类目、术语、人工覆盖和模型结果字典；
- 增量字典标准化、统一 Review Queue、术语候选管线；
- ES/ZH 两个独立无图导出，以及 Template 1 三表无图导出；
- ES/ZH 带图导出（只读取本地 250×250 白底衍生图，缺图保留 SKU）；
- 图片资产增量同步、断点 Manifest、标准化和质量状态；
- SQLite V2 事务 Writer、PRIMARY 只读 Repository、Shadow/Primary 模式接线（当前正式主链为 SQLite PRIMARY）；
- AI-Free 字典覆盖率、字段级 Resolver、Dictionary Apply 预览与正式 Gate（生产写入默认关闭）、统一审核队列和术语候选。
- Architecture V2 Extraction、Saved View、Selection Set、Selection Artifact 和 localhost Workspace 查询入口。

当前准确状态、已提交和仅存在于本地工作区的功能区别，见 [CURRENT_STATE](docs/CURRENT_STATE.md)。

## 核心数据流

```text
Action ES
  ↓
Sitemap / Listing / Nuevo / Promoción
  ↓
Presence 冻结 → Lifecycle → QA
  ↓                    ↓
Snapshot / Staging   QA FAIL：只留证据
  ↓ QA PASS
Master / State
  ├─ Dictionary / Review
  └─ Export
```

架构和规则详见：

- [整体架构](docs/ARCHITECTURE.md)
- [数据模型](docs/DATA_MODEL.md)
- [QA 规则](docs/QA_RULES.md)
- [开发路线图](docs/ROADMAP.md)
- [生命周期专题](docs/LIFECYCLE_ARCHITECTURE.md)
- [字典专题](docs/DICTIONARY_ARCHITECTURE.md)
- [导出模块](docs/EXPORT_ARCHITECTURE.md)
- [Template 1](docs/EXPORT_PROFILE.md)
- [Architecture V2](docs/ARCHITECTURE_V2.md)
- [Boundary Contracts V2](docs/BOUNDARY_CONTRACTS_V2.md)
- [Extraction Contract V1](docs/EXTRACTION_CONTRACT_V1.md)
- [Data Workspace V1](docs/DATA_WORKSPACE_V1.md)

## 快速使用

在 `F:\ActionSKUTracker` 中运行：

```powershell
$env:PYTHONPATH = "src"

# 查看状态
python -m action_tracker status

# 从正式 Master 建立初始状态
python -m action_tracker init-baseline

# 默认 dry-run：采集并生成证据，不写正式 Master
python -m action_tracker daily-run --dry-run

# 正式运行：只有 QA 允许时才提交
python -m action_tracker daily-run --no-dry-run

# 基于最近 snapshot 重跑 QA
python -m action_tracker qa

# 统计当前正式 CURRENT 的 AI-Free 字典覆盖率（只读）
python -m action_tracker dictionary-coverage --run-id <正式run_id>

# 生成字典字段应用预览；默认绝不写 Master
python -m action_tracker dictionary-apply --run-id <正式run_id> --dry-run

# 请求正式 Apply（当前配置会安全拒绝）
python -m action_tracker dictionary-apply --run-id <正式run_id> --commit

# 统一商品查询（只读 SQLite PRIMARY）
python -m action_tracker extract --status CURRENT --max-price 2 --limit 50 --json
python -m action_tracker saved-view create "低价在售" --query-json '{"statuses":["CURRENT"],"max_price":2}'
python -m action_tracker selection create "采购清单" --query-json '{"statuses":["CURRENT"],"max_price":2}'

# 完整回归测试
python -m pytest -q
```

详情阶段与 Presence 分离：

```powershell
python -m action_tracker detail-retry --run-id <run_id>
python -m action_tracker detail-apply --run-id <run_id>
python -m action_tracker detail-backfill --run-id <run_id>
```

## 字典

Git 中的正式基线位于 `data/dictionary/`；本机运行区位于 `runtime/dictionary/`。运行区包含构建结果、备份、审计和临时审核文件，不进入 Git。

```powershell
$env:PYTHONPATH = "src"
python scripts/build_dictionary.py
python scripts/audit_dictionary.py
python scripts/publish_dictionary_baseline.py
```

增量字典、审核队列、术语候选和 Resolver 已实现为本地离线能力；Dictionary Apply Gate 已实现，生产写入由 YAML 布尔值 `dictionary_apply.production_enabled: false` 明确关闭（字符串配置会安全报错）。正式 Apply 还要求字典与已发布基线逐文件 hash 一致、审计未过期、全部 SKU 为 AUTO_READY，且默认不允许 PROVISIONAL 品牌。具体状态见 [CURRENT_STATE](docs/CURRENT_STATE.md)。

Knowledge Production V1（P3–P6）的统一合同、SQLite resolution/queue/candidate/audit 表、字段级 Resolver、增量队列、候选 Validator 和 Auto-Approval Shadow 已完成；生产 Apply、AI provider、Scoped Dictionary 审批和 Auto-Approval 仍由配置门禁关闭。合同文档见 [`docs/knowledge/`](docs/knowledge/)。

## 导出

目标 Template 1 是一个 Excel、三张工作表：

1. `商品上下架明细`；
2. `今日西班牙语清单`；
3. `今日中文清单`。

第一张表按日期写 0/1；第二、三张表只包含当日有效 CURRENT SKU；只有中文表允许嵌入本地 250×250 白底图片。当日数量以正式 Listing/CURRENT 有效集合为准，不以 Sitemap 原始数量为准。

Template 1 无图三表已经可以从正式 run 生成；现有基础无图文件仍可单独导出：

```powershell
python -m action_tracker export --lang es --no-images --date YYYY-MM-DD
python -m action_tracker export --lang zh --no-images --date YYYY-MM-DD
# 读取本地图片并导出带图版本（不会触发网络下载）
python -m action_tracker export --lang zh --with-images --date YYYY-MM-DD
# Selection 导出（成员固定，事实取导出时最新 SQLite 数据）
python -m action_tracker export --lang zh --no-images --date YYYY-MM-DD --selection-id <selection_id>
# Template 1：只有“今日中文清单”嵌入本地图片，另外两张表保持无图
python -m action_tracker export-template1 --date YYYY-MM-DD --with-images
# 图片同步（只针对正式 CURRENT 的 image_url）
python -m action_tracker image-sync --date YYYY-MM-DD
python -m action_tracker image-status
# SQLite 状态、完整性和兼容导出确认（SQLite PRIMARY）
python -m action_tracker db-status
python -m action_tracker db-validate-production
python -m action_tracker sync-exports
# 仅在 Shadow 对账、备份与回滚验收完成后显式提升（不会自动发生）
python -m action_tracker db-promote-primary
# 导出历史 Presence 矩阵（只读历史来源，不访问官网）
python -m action_tracker export-history --date YYYY-MM-DD
```

图片同步与带图导出已经实现为独立阶段；历史 Presence 已可独立导出。详见
[Export 落地计划](docs/EXPORT_IMPLEMENTATION_PLAN.md)。

## 主要目录

| 路径 | 用途 | 是否进入 Git |
| --- | --- | --- |
| `src/action_tracker/` | 采集、生命周期、QA、字典和导出代码 | 是 |
| `config/` | 站点、阈值、类目、术语和 Profile 配置 | 是 |
| `data/dictionary/` | 审计通过的正式字典基线 | 是 |
| `runtime/master/` | 正式工作 Master | 否 |
| `runtime/snapshots/` | 每轮原始与标准化证据 | 否 |
| `runtime/state/` | 跨日生命周期状态 | 否 |
| `runtime/dictionary/` | 本机字典运行区 | 否 |
| `runtime/images/` | 本地图片缓存 | 否 |
| `runtime/exports/` | 导出文件与 manifest | 否 |
| `tests/` | 回归测试 | 是 |

## 安全边界

- `F:\按日期整理` 永远只读；
- `F:\Action_Master\Action_Master.xlsx` 只允许读取或复制；
- QA FAIL 和 dry-run 不得覆盖正式 Master/State；
- 不绕过 CAPTCHA、Cloudflare 或其他网站安全机制；
- 不每天全量抓详情、全量翻译或全量下载图片；
- 不使用 Sitemap-only SKU 冒充当日有效在售商品；
- 当前 `storage.mode: SQLITE_PRIMARY`；SQLite 保存 Product/Lifecycle Structured Truth，Excel/CSV 是由 SQLite HEAD 生成的兼容投影。
- SQLite PRIMARY 提交前包含本地化覆盖率防回退门禁；经审计确认的数据回退只能使用 `db-repair-localization-regression` 定向恢复。

开发和提交规则见 [AGENTS.md](AGENTS.md)。

## CI

GitHub Actions 会在 push 和 Pull Request 时自动运行云端安全测试。

CI 只运行不访问官网、使用临时目录的本地测试，不执行：

- Action 真实网站采集；
- Playwright 真实抓取或浏览器安装；
- 正式 Master/State 写入；
- runtime 生产状态修改；
- 字典 baseline 发布、图片下载、push 或 merge。

完整真实运行仍必须在本地经过 dry-run 和 QA。CI 测试白名单见
`tests/ci_safe_tests.txt`，依赖入口见 `requirements.txt` 与
`requirements-dev.txt`。
