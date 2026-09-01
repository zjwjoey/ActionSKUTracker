# Localization Intelligence V1 验收记录

本分支以 `CHINESE_LOCALIZATION_STANDARD_V1`、`NAMING_AND_SPEC_PLANNING_STANDARD_V1` 为策略基线。稳定主线基线为 `a3cbb6bfb49431d7ddf4dc502d302e6efb44a3f2`；本分支完成后在 feature 分支运行 CI，等待用户决定是否合并。

## 已实现边界

- `src/action_tracker/localization/` 提供 SourceFacts → SemanticFacts → LocalizationPlan → Validator 的唯一确定性核心路径。
- 七个中文字段均带 value、source、status、source_hash、freshness_status、policy_version、review_reasons、provenance。
- 品名与规格规划分离；技术型号/接口/单位保留，规格统一使用 `×`、`–`、`｜` 和紧凑单位。
- 四个版本化知识 CSV 会安全初始化，manifest 记录哈希；已有字典只读复用。
- AI provider 只允许显式 UNKNOWN 适配器调用，默认 DisabledProvider；密钥仅从环境变量读取。
- learning candidates 聚合到 `runtime/localization/reports/<run_id>/`，不会伪造人工确认。
- `localization-enrich` / `localization-audit` / `localization-learning-report` / `localization-promote` / `localization-apply` CLI 已提供；apply 默认 dry-run，生产开关关闭时拒绝正式写入。
- 本轮补齐本地 OpenAI-compatible Provider（可配置 Ollama/LM Studio/vLLM/Qwen 等端点）、严格 JSON prompt/response contract、只读 `localization-ai-status` 与虚构 SKU `localization-ai-check`。
- AI 候选现在与确定性候选进入同一 Learning Pool；`KnowledgePromotionRouter` 仅在显式人工批准、Validator PASS、source hash 通过且无冲突时写入所属知识 CSV，并原子更新治理 manifest；不执行 Git、PRIMARY 或自动批准。
- Review Queue 增加可选 candidate/knowledge 元数据，并使用 `REVIEW_FIELD_TO_OVERRIDE_FIELD` 做字段级旧字典适配；描述/详情不会被强塞进旧商品覆盖表。
- `Sin alcohol`、`Sin cafeína`、`Sin azúcar`、`Sin gluten` 保持否定语义；SemanticFact 增加 coverage 状态，Validator 对未覆盖事实报 `FACT_NOT_COVERED`。

## 验收命令

```text
PYTHONPATH=src python -m pytest -q
PYTHONPATH=src python -m action_tracker localization-audit --current
PYTHONPATH=src python -m action_tracker localization-apply --run-id <run_id> --dry-run
```

正式 apply 必须同时满足 SQLite PRIMARY、显式 `knowledge.production_apply_enabled=true`、Validator PASS 和审批状态；本分支不自动开启该开关。

## 不变量

Localization 核心不访问官网，不修改 Presence、Lifecycle、Price history、ES 事实、图片或 Selection/Artifact。普通日常提交不应将既有中文 STALE 无条件改回 CURRENT；正式修正必须使用独立 correction commit 并保留来源哈希、审批和字段 provenance。

## 本次 feature 验证记录

- implementation feature head：本轮提交完成后以 `git rev-parse HEAD` 记录（不预写未来 SHA）。
- 基线：`a3cbb6bfb49431d7ddf4dc502d302e6efb44a3f2`
- 初次全量 dry-run（`v1-feature-audit-20260901b`）曾错误地把 5,379 条全部标记为 `SOURCE_HASH_CHANGED`。根因是 V1 JSON hash 与 SQLite canonical hash 不一致；该报告不再作为质量基线。
- Hash closure 后，V1 直接复用 SQLite 的 canonical `localization_source_hash`；PRIMARY 已完成仅新增列的迁移，`product_localizations` 现可持久化第七字段 `unit_price` 及其 `unit_price_source`。
- 全量只读 audit：5,379 CURRENT；ready 376、review_required 5,003、普通西语残留 1,716、数字事实 mismatch 334、真实 `SOURCE_HASH_CHANGED` / `STALE_LOCALIZATION` 各 13、AI calls 0（`v1-determinism-a-20260901`）。
- 相同 PRIMARY head 上连续两次独立进程 audit 的 `localization_audit.csv` SHA-256 完全一致：`DDFE33529BBA5DDE0C51F52478148C15DFA1645796A57385F81D89B3BFFB143B`；品牌集合已排序，避免 Python set 遍历导致非确定性。
- 第七字段的历史存量尚未被反写；当前 Audit/Export 会从官方单价实时规范化，之后只有通过正式 Apply Gate 的行才写入该字段。
- `localization-apply --dry-run` 已执行（run `v1-feature-apply-dry-run-20260901`），未写入 PRIMARY。
- GitHub Actions exact-head CI：Ubuntu 与 Windows 均 PASS，run `33447471041`（head 与 implementation feature head 一致）。
- 本轮补充了 SourceFacts 官方来源字段、SemanticFact 证据字段、跨字段数字保护、知识 CSV 唯一键/schema 校验、AI 身份/价格/结构校验、Apply 源哈希门禁，以及 unchanged daily 的中文 provenance/updated_at 保留；Hash closure 后本地回归共 382 项通过。
- 最终补充回归后本地全量测试：`385 passed`（目标 feature worktree，含 Learning E2E）。本机 Qwen 未启用，`localization-ai-status` 返回 `DISABLED`，因此没有网络依赖。
- 只读 CURRENT 审计（使用现有 PRIMARY 数据库快照，未写入 PRIMARY）：5,379 CURRENT；READY 394、REVIEW_REQUIRED 4,985；普通西语残留 1,671；数字事实 mismatch 334；FACT_NOT_COVERED 0；AI calls 0；AI avoidance 100%。

当前结论：`LOCALIZATION_V1_NOT_ACCEPTED DO_NOT_MERGE`。原因是全量质量门禁尚未 PASS（仍有西语残留、数字事实 mismatch 和待审候选），且生产 AI/apply/auto-approval 开关保持关闭。Learning/Promotion/Review Queue/Provider 合同已闭合，但不能把未审核候选直接当作正式中文结果。
