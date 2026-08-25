# Action 西班牙站商品每日监测程序

长期监测 Action 西班牙官网（`action.com/es-es`）的商品变化，并维护一套可审计的本地商品字典：新品、下架、重新上架、价格变化、促销起止、Nuevo 标签、可持续标识和内容变化。

项目采用本地文件型存储（Excel / CSV / JSON），不依赖数据库、云端或 Web 后台。阈值和路径集中在 `config/settings.yaml`。

## 架构

```text
daily.py
  ├── Listing / Sitemap  → Presence 事实、SKU、价格、标签
  ├── Detail Enrichment  → 仅补充变化或待补详情的商品
  ├── Lifecycle          → NEW / ACTIVE / MISSING / REAPPEARED / OFFLINE
  ├── QA Gate            → 不完整观测或质量失败时阻断正式写入
  ├── Excel Writer       → 原子更新本地 Master
  └── Dictionary Layer   → 标准品名、品牌、分类、术语、人工覆盖
         ├── runtime/dictionary/  本机运行区
         └── data/dictionary/     Git 追踪的正式字典基线
```

核心原则（详见 `AGENTS.md`）：

- 西语官网信息是事实数据，中文是派生数据。
- 不每天重抓全部商品详情，先发现变化、只处理变化的 SKU。
- 历史文件（`F:\按日期整理`）与原始 Master（`F:\Action_Master\Action_Master.xlsx`）永远只读。
- 不绕过 CAPTCHA / Cloudflare 安全机制。
- QA 失败时保留证据，但不覆盖正式 Master。

## 字典与 Git 基线

`runtime/dictionary/` 是本机运行目录，包含每日构建结果、审计报告、备份和临时复核文件，不进入 Git。

`data/dictionary/` 是经过审计后发布到 Git 的稳定基线，当前包括商品、品牌、分类、术语、人工覆盖、模型译文、源数据损坏报告，以及带 SHA-256 校验值的 `baseline_manifest.json`。

新工作区首次构建时，如 `runtime/dictionary/` 不存在，程序会从 `data/dictionary/` 基线读取已有字典结果。发布前会重新执行审计；任何 `FAIL` 都会阻断发布。

```powershell
$env:PYTHONPATH = "src"
python scripts/build_dictionary.py
python scripts/audit_dictionary.py
python scripts/publish_dictionary_baseline.py
```

更多字段和状态说明见 [字典架构文档](docs/DICTIONARY_ARCHITECTURE.md)。

## 使用

```powershell
$env:PYTHONPATH = "src"

# 建立基线状态文件（从 runtime/master/Action_Master.xlsx）
python -m action_tracker init-baseline

# 每日 dry-run（出证据，不写 Master）
python -m action_tracker daily-run --dry-run

# 状态 / QA
python -m action_tracker status
python -m action_tracker qa

# 测试
python -m pytest -q
```

## 目录

- `src/action_tracker/`：采集、生命周期、Excel 与字典核心逻辑
- `config/`：站点、阈值、15 个固定中文一级类目和术语种子
- `data/dictionary/`：Git 追踪的正式字典基线
- `runtime/`：本机 Master、快照、暂存、运行时字典、日志与备份（忽略）
- `scripts/`：字典构建、审计、发布与人工复核辅助脚本
- `tests/`：回归测试

## 提交约定

提交时仅选择代码、配置、文档和 `data/dictionary/` 基线。不要使用 `git add .`；`runtime/`、图片、导出、报告和临时文件不应提交。
