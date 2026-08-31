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

- feature head：`a53c7ca6296d8ff133ed39055895c76d13562b0d`
- 基线：`a3cbb6bfb49431d7ddf4dc502d302e6efb44a3f2`
- 全量 dry-run：5,379 CURRENT；ready 0、review_required 5,379、普通西语残留 5,379、数字事实 mismatch 436、AI calls 0。
- 该结果反映当前 PRIMARY 中大量历史中文字段的来源哈希/语言状态仍待重新标准化，不代表系统绕过了审核；formal apply 不会对这些行自动写入。
- GitHub Actions exact-head CI：Ubuntu 与 Windows 均 PASS，run `33437434835`。

当前结论：`LOCALIZATION_V1_NOT_ACCEPTED DO_NOT_MERGE`。原因是全量质量门禁尚未 PASS，且生产 AI/apply 开关保持关闭；需先完成 UNKNOWN 候选审核或提供新的有效中文结果，再重新运行 audit/apply 验收。
