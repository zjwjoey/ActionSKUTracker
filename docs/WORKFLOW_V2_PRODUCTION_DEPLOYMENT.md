# Workflow V2 Phase 1 生产部署说明

## 候选边界

- 部署分支：`deploy/workflow-v2-production-20261004`
- 不可变候选：`production/workflow-v2-phase1-rc4`
- 默认 `config/settings.yaml` 保持 fail-closed；生产 profile 是 partial overlay，不能单独作为完整 settings 文件加载。
- 本文示例不引用旧的 `75b01acc`、RC3 或 branch HEAD。

## Phase 1 运行契约

运行时将 base settings 与显式 profile deep-merge，并在每次 run evidence 中记录：base/profile 路径、两个 SHA-256 和 effective config hash。API key 只记录 `SET` / `NOT_SET`，不记录值。

Phase 1 只允许 PRIMARY 西语事实、registry ingest、Qwen 翻译、QA 和 review state。以下开关必须关闭：

- `workflow_v2.auto_policy_approval.enabled`
- `workflow_v2.auto_export.enabled`
- `localization.production_apply_enabled`
- `knowledge.production_apply_enabled`
- `knowledge.fallback_to_spanish`

### 唯一 Phase 1 命令

```powershell
python -m action_tracker data-update-v2 `
  --date YYYY-MM-DD `
  --profile config/workflow_v2_production_profile.yaml `
  --production-translation `
  --no-dry-run
```

`--production-translation` 必须有显式 `--profile`，或由已审计的 `ACTION_TRACKER_CONFIG_PROFILE` 提供。显式 CLI 参数优先于环境变量。Phase 1 **DO NOT USE `--production-apply`**；不得执行 localization apply、auto approval 或 formal export publish。

`--production-apply` 仅属于后续独立授权的 Phase 2，且必须同时打开 knowledge/localization apply gates。它不是 Phase 1 的替代参数。

## 生产运行前置顺序

1. 备份 PRIMARY。
2. 使用 RC4 做 production preflight。
3. 核对 audited ref、base/profile SHA 和 effective config hash。
4. 确认 `DASHSCOPE_API_KEY` 为 `SET`。
5. 执行上面的 Phase 1 translation-only 命令。
6. 检查 fact commit、registry、queue、provider calls、QA 和 review evidence。
7. 确认 `localization_apply=disabled`、`export_publish=disabled`；不得自动 Apply/Export。

## 只读 preflight

```powershell
$env:PYTHONPATH='F:\ActionSKUTracker_workflow_v2\src'
python scripts/workflow_v2_production_preflight.py `
  --source-root F:\ActionSKUTracker_workflow_v2 `
  --profile F:\ActionSKUTracker_workflow_v2\config\workflow_v2_production_profile.yaml `
  --data-root F:\ActionSKUTracker `
  --expected-branch deploy/workflow-v2-production-20261004 `
  --expected-ref production/workflow-v2-phase1-rc4 `
  --json
```

Preflight 与正式命令必须使用同一 base settings、同一 profile 和同一 effective config hash。Preflight 是只读的，不调用 Qwen、不 claim queue、不写 PRIMARY。

## 数据和恢复边界

生产数据库仍是唯一 PRIMARY。验证只能使用 temporary SQLite、fixture、Fake Provider 和 mock environment；不得清空历史 queue、调用真实 Qwen、执行全量 Action 采集或发布 Excel。Resume 必须沿用原 run 的 business date、source commit 和 config hash；profile 改变时阻断并报告 `CONFIG_CHANGED_SINCE_RUN`。

## 发布标记

- `PRODUCTION_CODE_CUTOVER`: preflight、备份、完整测试和 RC4 exact-head CI 全部通过后才可标记 `READY`。
- `PRODUCTION_FULL_AUTOMATION`: auto approval 和 auto export 关闭期间保持 `NOT_YET`。
