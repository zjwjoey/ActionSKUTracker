# Workflow V2 架构与生产边界

## 三层职责

1. 每日事实：既有 orchestrator.daily 负责采集、QA、Presence/Lifecycle、Price/Event、完整 CommitBundle、PRIMARY 和兼容投影。
2. 增量中文：Registry → 本来源 run Queue → TM/Terminology/授权 Provider → Fact/Canonical QA → Owner → immutable patch → Apply。
3. 发布：FinalZhProjection → Repair → ES/ZH Audit → Parity → Strict Gate → 三件套。

V2 是可恢复编排，不是另一套 Lifecycle/Price 算法。FACT_COMMITTED 或 Phase 1 SUCCESS 不代表已 Apply/正式发布。

## FACT_COMMIT

旧部分 bundle 缺失 Lifecycle、ABSENT、Price/Event 和 Collection Quality。生产模式禁止该 writer：

    data-update/production-run 提交有效 daily
    → fact_adapter 读取同日 committed daily
    → 核验 QA、持久化 Collection Quality hash、当前 SKU/六字段事实与 PRIMARY HEAD
    → VERIFIED_DAILY_FACT_COMMIT_REUSED，事实写入次数 0

它不重新采集、推进生命周期或覆盖中文。来源变化、质量缺失、日期不一致、未先完成 daily 均 BLOCKED；不能用 requires_collection_integrity=False 退回旧生产路径。fixture/canary 的独立 bundle 只允许临时 SQLite，不是正式观察。

生产 V2 不重新抓详情/Apply；待补详情走现有 daily detail-retry/Apply 合同。

## 来源、队列、恢复和锁

生产 queue scope 为实际 collection_run_id；workflow_run_id 是编排身份。plan、worker、QA、policy、Apply 使用同一来源 run，禁止消费历史全局队列。无变化不调 Provider。

默认 Apply/auto approval/AI 关闭；显式 Phase 1 profile 禁止 auto approval/正式 Apply/export；Owner source-bound approval 不可跳过。

Resume 恢复冻结 context/records/stages；配置 hash 变化拒绝；生产绑定 head 改变拒绝旧恢复。Apply 自身绑定 base，成功后更新允许的 head。生产模式共享 paths.state/daily-run.lock，不嵌套 collector 锁。

## 发布

ES 在 staging 的 preview 子目录输出，ZH 通过 staging 的完整正式 Gate；最终发布验证中文 actual/audited hash 与双语事实 parity。普通 preview 永不写正式目录。Template 1 与普通中文清单共用 validate_production_release，Repair Audit 不是完整 Gate 的替代品。

    python -m action_tracker data-update-v2 --date 2026-10-08 --profile config/workflow_v2_production_profile.yaml --production-translation --no-dry-run
    python -m action_tracker data-update-v2 --date 2026-10-08 --fixture fixture.json --fake-provider

第二条用于离线回归；第一条须同日 committed daily、授权与有效 profile。证据在 runtime/reports/workflow_v2/<date>/<workflow_run_id>/，记录来源身份、fact/localization commit、配置 hash、QA 和发布状态。SUCCESS_WITH_PENDING 不等于正式发布。参见 [README](../README.md)。
