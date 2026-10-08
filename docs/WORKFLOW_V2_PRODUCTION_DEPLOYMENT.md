# 生产部署与 Phase 1–3 授权

## 版本

2026-10-08 核实 main 231550eb74d24c927e100bc585aa728bf7ab7f19；冻结 production/phase1-3-20261007。收尾分支 fix/post-production-repository-closure-20261008 尚未合并/部署。部署应引用审查通过且 exact-head Ubuntu/Windows CI 全绿的不可变候选 SHA。

本轮不带入 3d28f0b snapshot-ingest；不合并旧 PR #5。

## 部署顺序

1. 独立 checkout 完成 full pytest、CI-safe、生产副本核验。
2. 推分支、PR、exact-head CI 与审查。
3. Owner 授权合并/部署后冻结新 SHA；停止写入并做 SQLite Backup API 备份。
4. 更新批准代码/配置，不 reset PRIMARY、不用旧 Excel 覆盖。
5. 核验 DB identity/schema/integrity/foreign keys、head、投影、锁、参数。
6. 分阶段启用权限并记录真实证据。候选测试 PASS 不等于已部署。

## Phase 1

    powershell -ExecutionPolicy Bypass -File scripts/run_production_daily.ps1 -Date 2026-10-08 -ProjectRoot F:\ActionSKUTracker
    python -m action_tracker data-update-v2 --date 2026-10-08 --profile config/workflow_v2_production_profile.yaml --production-translation --no-dry-run

先完成正式 daily，唯一事实算法负责 Presence、缺失/下架、NEW/REAPPEARED、Price/Event 和完整 QA bundle。V2 只复用同日 committed daily，不能绕过质量证据。

Profile 是 base settings partial overlay，经 deep merge/Phase 1 validator；记录 base/profile/effective hash，密钥仅 SET/NOT_SET。Phase 1 关闭 auto approval、auto export、knowledge/localization Apply、Spanish fallback。Qwen 仅处理该来源 run 的新增/变化字段。

## Phase 2 / 3

QA 和 Owner 来源绑定审批后生成 immutable patch，以当前 base Apply。production-apply 是后续独立授权，不是 Phase 1 替代参数，不自动打开审批权限。

中文 export --research-release 或 export-template1 --research-release 使用完整来源/Master Quality/Repair/Audit/parity/实际行 hash 门禁；通过才正式发布。Preview 独立目录，不能覆盖正式文件。V2 ES staging 仍需最终双语 Gate。

## 观察与恢复

观察连续 daily 的字段增量、同日 Resume 幂等、缺失/重现/价格、Registry、发布 Gate。Registry 单独失败保持事实并 DEGRADED，只恢复该 run；不能将某日数据缺口当作改事实授权。

本轮不改 Task Scheduler/GitHub 管理设置。main protection 需另获 Owner 授权；没有已验证上一生产版本不自动回滚，参见 [Rollback](WORKFLOW_V2_PRODUCTION_ROLLBACK.md)。
