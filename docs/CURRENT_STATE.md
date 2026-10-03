# Action SKU Tracker 当前状态

更新时间：2026-10-03
基线：`origin/main@b47e4c56a0274fdddceff347e2571d77c1476014`

## Canonical branch

`main@b47e4c56a0274fdddceff347e2571d77c1476014`

## 稳定主线

- SQLite PRIMARY 是生产主链和唯一正式读事实源。
- Excel、CSV 和导出文件是兼容投影或只读交付物，必须绑定当前 committed head 并通过 `export_sync`。
- 主链顺序为：官网证据采集 → Presence 冻结 → Lifecycle → QA → Snapshot/Staging → CommitBundle/事务 → SQLite PRIMARY → 兼容投影/只读导出。
- Detail 只补充字段，不改变 Presence、CURRENT、MISSING 或 OFFLINE 结论。
- `F:\按日期整理` 与历史 Master 只读；生产 runtime 不进入 Git。

## 中文本地化安全边界

- 中文是西语官网事实的派生数据；西语事实、SKU、价格、链接、型号、技术 token、数字和单位不可被翻译流程改写。
- 正式中文采用 NO_BRAND 展示策略：品牌/IP 留在内部证据、QA 和术语上下文，正式展示字段不显示品牌。
- 缺失、过期、源损坏或未确认字段保持 `PENDING`/`REVIEW_REQUIRED`；不得把西语复制到正式中文。
- `display_fallback=ES` 仅为展示提示，不能改变审批、QA 或 release readiness。
- 当前安全默认值：production apply、auto approval、AI provider 和 Spanish fallback 均关闭。

## 当前代码状态

- Translation System V1 安全默认值已在主线合并。
- 本闭环工作在 `chore/repository-consolidation-closure-v1`，从上述基线创建并已推送；本分支只提交文档、审计和安全的实验分支编排，不执行生产采集、apply 或导出写回。
- CI 白名单来自 `tests/ci_safe_tests.txt`；完整回归和 CI-safe 回归均需使用临时 fixture，不触碰生产 PRIMARY。

## 待审计边界

- 远端旧分支保留用于证据和 owner review；本闭环不删除分支、不移动 recovery tag、不直接修改或推送 main。
- Stage6 provenance 已在 `fix/stage6-provenance-v2` 验证，Scrapling detail shadow 已在 `experiment/scrapling-detail-shadow-v3` 验证；实验依赖只能放在 `requirements-experiments/`，不得进入生产链。
- 命名历史只做迁移盘点，不直接合并旧分支。

## 当前已知缺口

- GitHub `main` 尚未配置 branch protection/ruleset，需要管理员配置 PR、required checks、禁止 force-push 和禁止删除。
- 任何真实 daily-run、官网采集、production apply 和真实生产数据验收均不属于本闭环，仍需单独运行与审批。

## 证据位置

- 架构与边界：`docs/ARCHITECTURE.md`、`docs/DATA_MODEL.md`、`docs/QA_RULES.md`
- 分支清单：`docs/audits/REPOSITORY_BRANCH_INVENTORY_20261003.md`
- 本轮预检：`docs/audits/REPOSITORY_CLOSURE_PREFLIGHT_20261003.md`
- 本轮闭环：`docs/audits/REPOSITORY_CLOSURE_20261003.md`
