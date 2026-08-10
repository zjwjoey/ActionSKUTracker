# Action 西班牙站商品每日监测程序

长期每日监测 Action 西班牙官网（action.com/es-es）的商品变化：
新品、下架、重新上架、价格变化、促销起止、官网 Nuevo 标签、可持续标识、内容变化。

当前阶段：**文件型存储**（Excel / CSV / JSON），不引入数据库、云端、Web 后台。
所有阈值集中在 `config/settings.yaml`。

## 架构

```
Orchestrator (daily.py)
    ├── SKU Monitor    sitemap + listing -> NEW/ACTIVE/MISSING/REAPPEARED
    ├── Product Updater  只处理变化的 SKU（补详情/价格/标签）
    ├── Translator       中文 fallback 西语（阶段一不做 AI 翻译）
    ├── STAGING          每日变化暂存 runtime/staging/<run_id>/
    ├── QA Gate          校验通过才允许写 Master
    └── Excel Writer     原子更新 Action_Master.xlsx（dry-run 不启用）
```

核心原则（详见 `AGENTS.md`）：
- 西语官网信息是事实数据，中文是派生数据。
- 不每天重抓全部商品详情，先发现变化、只处理变化的 SKU。
- 历史文件（`F:\按日期整理`）与原始 Master（`F:\Action_Master\Action_Master.xlsx`）永远只读。
- 不绕过 CAPTCHA / Cloudflare 安全机制。

## 使用

```bash
# 建立基线状态文件（从 runtime/master/Action_Master.xlsx）
python -m action_tracker init-baseline

# 每日 dry-run（出证据，不写 Master）
python -m action_tracker daily-run --dry-run

# 状态 / QA
python -m action_tracker status
python -m action_tracker qa

# 测试
python -m pytest tests/
```

## 目录

- `src/action_tracker/` 程序本体
- `config/settings.yaml` 全局配置（所有阈值集中于此）
- `runtime/` 运行时数据：master / snapshots / staging / state / backups / logs
- `tests/` 回归测试（规范 §60 的 24 项）
