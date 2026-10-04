# Workflow V2 生产部署说明

## 发布对象

本次部署只发布经过验证的部署分支：

- 分支：`deploy/workflow-v2-production-20261004`
- 代码：`2cac2b7e60e01d6468af21ec3eb9caafab6777ac`
- 基线生产分支：`fix/naming-history-export-20260917`，旧 head：`3a9df7af807875b97d4eab1b5d3bb57c69f5c041`

部署分支直接从已验证的 Workflow V2 feature head 创建。不得把 feature 分支合并到命名历史分支，也不得 cherry-pick 命名历史分支。

## 生产开关

部署配置开启 Workflow V2、只读 shadow preflight 和 Qwen MT 翻译。模型调用入口为 `localization.ai`，provider 为 `qwen_mt`，model 为 `qwen-mt-flash`，密钥只从 `DASHSCOPE_API_KEY` 读取。同步翻译批次上限为 50。

以下开关保持关闭：`auto_policy_approval`、`auto_export`、`detail_retry`、旧 `translation.qwen_mt` 入口，以及西语回填。自动审批和自动发布必须另行授权。

第一阶段还保持 `knowledge.production_apply_enabled`、`localization.production_apply_enabled` 和 `dictionary_apply.production_enabled` 的现有安全门禁关闭。Qwen 结果进入 QA/Review 状态，不直接覆盖正式中文投影；需要正式 Apply 时另行审计授权。

## 上线前操作

1. 在部署分支运行完整 pytest 和 Workflow V2 fixture canary。
2. 运行只读预检（不会调用 Qwen，也不会写 PRIMARY）：

   ```powershell
   $env:PYTHONPATH='F:\ActionSKUTracker_workflow_v2\src'
   python scripts/workflow_v2_production_preflight.py `
     --source-root F:\ActionSKUTracker_workflow_v2 `
     --config F:\ActionSKUTracker_workflow_v2\config\workflow_v2_production_profile.yaml `
     --data-root F:\ActionSKUTracker `
     --expected-branch deploy/workflow-v2-production-20261004 `
     --expected-head 2cac2b7e60e01d6468af21ec3eb9caafab6777ac `
     --json
   ```

3. 确认预检为 `PASS` 后，由 Owner 执行代码目录切换，并采用该 profile 的生产配置。切换动作不复制、不重建、不替换 `F:\ActionSKUTracker\runtime`。仓库默认 `settings.yaml` 保持 fail-closed，避免普通开发命令隐式调用 Qwen。
4. 切换后先执行一轮小批量生产验证；不得直接运行全量历史翻译队列，不得打开自动审批或自动导出。

## 备份与数据边界

部署前备份清单位于：
`F:\ActionSKUTracker\runtime\backups\workflow_v2_production_20261004\deployment-manifest.json`。

同目录包含 SQLite PRIMARY 的一致性备份、生产配置备份、源数据库和配置 SHA-256。生产数据库仍是唯一 PRIMARY；历史队列保留在库内，由运行时按当前日优先和每批 50 条隔离处理。

本次收尾没有执行真实详情抓取、真实 Qwen 请求、生产导出或生产代码目录切换。

## 发布结果标记

- `PRODUCTION_CODE_CUTOVER`: 只有预检、备份、测试和 CI 均通过后才标记 `READY`。
- `PRODUCTION_FULL_AUTOMATION`: 在自动审批和自动导出保持关闭期间标记 `NOT_YET`。
