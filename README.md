# ActionSKUTracker

Action 西班牙站 SKU 自动监测、生命周期管理、西语事实入库、增量中文本地化、严格审计和 Excel 发布系统，面向 Owner、运营人员、开发人员及 AI Coding Agent。

## 权威数据与版本

- 官网西语是商品事实来源；SQLite PRIMARY 是唯一正式存储和读取来源。
- 中文是来源绑定的派生字段；Excel/CSV 是兼容投影或交付物，不能反向覆盖 PRIMARY。
- 2026-10-08 核实的 origin/main：231550eb74d24c927e100bc585aa728bf7ab7f19。
- 生产冻结标签 production/phase1-3-20261007 指向上述 SHA。
- Phase 1–3 曾在真实 PRIMARY 验收并发布当次 5,626 SKU。这不是固定商品数量，也不代表每天自动批准或发布。
- 收尾分支 fix/post-production-repository-closure-20261008 尚未合并/部署；本轮保持既有生产运行代码与配置不变。
- 今日实际执行 checkout 为 F:\ActionSKUTracker_workflow_v2，分支 fix/workflow-v2-phase3-final-closure@3d28f0b；它比已冻结 main 多 snapshot-ingest，不能把实际运行 checkout 与已审查 main 基线混为一谈。
- 3d28f0b 的 snapshot-ingest 是独立新功能，不纳入本轮，需要专项审查。

## 流程和三阶段

    Action 采集 → Source QA → Presence/Lifecycle → SQLite PRIMARY Fact Commit
    → Registry → 增量中文候选 → Fact/Canonical QA → Owner 审批
    → immutable patch → Apply → FinalZhProjection/Repair → Phase 3 Audit → 正式 Excel

| 阶段 | 职责与入口 | 放行条件 |
| --- | --- | --- |
| Phase 1 | data-update/production-run 提交事实；worker 或受控 V2 处理增量候选 | 采集质量、QA、预期 PRIMARY HEAD；Provider 显式授权 |
| Phase 2 | 来源绑定 Owner Review、immutable patch、Apply | 批准 revision、当前 source hash、QA、权限和 base commit |
| Phase 3 | FinalZhProjection → Repair → ES/ZH Audit → Parity → Excel | P0=0、unresolved blocking=0、完整 Release Gate PASS、审计行 hash 等于发布行 hash |

FACT_COMMITTED 只代表西语事实完成。Phase 1 成功不代表中文批准或正式发布。没有 Owner 审批不能宣称 Phase 2 完成；没有完整 Gate PASS 不能宣称 Phase 3 完成。

唯一 daily 事实算法仍在既有 collector。V2 生产模式只验证并复用已经通过 daily 质量审计且提交成功的同日事实，不独立提交缺少 Lifecycle、缺失推进、价格历史和 Collection Quality 的部分事实包。V2 不是另一套等价采集入口。fixture/canary 只写临时 SQLite，不能当作正式来源证据。

## 安装与数据根

    python -m pip install -r requirements-dev.txt
    $env:PYTHONPATH = "src"

代码与数据目录可分离。官方 wrapper 用 -ProjectRoot 指定数据根；直接 Python 使用 ACTION_TRACKER_PROJECT_ROOT。先核对有效配置证据，不能把临时 checkout 默认目录当成生产 PRIMARY。

| 路径 | 用途与边界 |
| --- | --- |
| runtime/db/ | PRIMARY；只有授权事务接口可写 |
| runtime/master/、runtime/state/ | 当前 committed head 的兼容投影；共享 daily-run.lock |
| runtime/snapshots/、runtime/staging/ | 按业务日期/run 保存来源、QA、提交证据 |
| runtime/reports/ | Operations、Workflow、Registry、审计报告 |
| runtime/backups/ | SQLite Backup API 一致性备份 |
| runtime/exports/preview/ | 普通 ES/ZH 和 Template 1 预览 |
| runtime/exports/ | 严格门禁后的正式发布；V2 先用受控 staging |
| runtime/images/ | 本地缓存及 250×250 白底衍生图；Export 不下载 |
| data/dictionary/ | 已审查术语、分类、品牌及规则基线；不能自动晋升候选 |

runtime 数据、报告、密钥、Cookie、图片和 Excel 不提交 Git。F:\按日期整理 和历史 F:\Action_Master\Action_Master.xlsx 永远只读。

## 正式命令

所有参数均对应实际 CLI；日期按 Europe/Madrid 业务日期。尖括号替换为真实来源绑定身份，不能原样执行。

### 每日事实

    python -m action_tracker data-update --date 2026-10-08
    python -m action_tracker production-run --date 2026-10-08
    python -m action_tracker production-run --date 2026-10-08 --resume --run-id <operations_run_id>
    python -m action_tracker daily-run --dry-run --fetch-details

data-update/production-run 自动执行 preflight、Backup、collector/QA/commit、Registry 状态检查、兼容导出同步和报告。图片与知识步骤受配置约束；中文审批默认关闭。Registry 失败保留事实并 DEGRADED。

daily-run --dry-run 是诊断/采集证据路径；SQLITE_PRIMARY 拒绝 daily-run --no-dry-run。旧 scripts/run_daily.ps1 默认委托官方 Operations wrapper，-DryRun 保留诊断语义。

### 增量中文：显式授权后

    python -m action_tracker translation-status
    python -m action_tracker translation-worker --run-id <collection_run_id> --limit 50 --provider --once
    python -m action_tracker data-update-v2 --date 2026-10-08 --profile config/workflow_v2_production_profile.yaml --production-translation --no-dry-run

Worker 的 --provider 允许使用配置 Provider，须有有效凭证与当前来源门禁；生成 Registry 候选，不直接写正式中文。V2 production translation 必须先有同日 committed daily，显式 profile 限定权限和本来源 run 队列，不能消费所有历史待翻译项。

### Owner 审批与 Apply

    python -m action_tracker workflow-v2-apply-owner-review --review-csv <review.csv> --owner-decisions-csv <owner.csv> --actor human:owner --report <approval-report.json>
    python -m action_tracker translation-apply --from-registry --run-id <apply_run_id> --base-commit-id <current_primary_head> --actor human:owner --dry-run
    python -m action_tracker localization-apply --run-id <id> --dry-run

审查 dry-run 并开启对应生产权限后才可用 --commit。保留 immutable revision/patch、审批身份、QA、逐字段来源与 Apply 审计；模型候选不等于 Owner 批准。localization-apply 是既有候选路径，不能绕过其门禁。

独立 translation-apply 的 --run-id 是 Apply 审计身份，不能当作来源队列过滤参数；dry-run 审查范围为全部可批准 Registry 候选。按 collection run 限定的 Apply 由 V2 queue_run_id 合同提供。

### 预览与正式发布

    python -m action_tracker export --lang es --no-images --date 2026-10-08
    python -m action_tracker export --lang zh --no-images --date 2026-10-08
    python -m action_tracker export-template1 --date 2026-10-08
    python -m action_tracker export --lang zh --no-images --date 2026-10-08 --research-release
    python -m action_tracker export-template1 --date 2026-10-08 --research-release

普通命令是 PREVIEW，只写独立 preview 目录。未知文件、损坏标记、正式标记均禁止预览覆盖。重复预览只允许替换摘要验证通过的 preview bundle。

--research-release 进入 PRODUCTION_RELEASE：SQLite 当前来源、完整 Master Quality、批准/新鲜度、ES/ZH 事实对账和 Repair/Release 全部通过才写正式目录。Template 1 共用同一完整 Gate。XLSX、manifest、repair-report 仍用原子替换及失败回滚；实际工作簿行 hash 必须等于审计行 hash。

ES preview 可以是 V2 双语正式发布的受控 staging 输入，最终发布仍检查中文严格 Gate 和双语 parity。导出离线只读，不调用模型、不写 PRIMARY、不猜事实。

## Translation System

固定六字段 canonical aggregate source hash 加字段 source hash。NULL/None/missing/空字符串等价；不把 "null"、"0"、"N/A" 当空值。Registry 保存 Source Version、Unit、immutable Revision、QA 和审批；Queue 按新增/变化字段调度；TM 复用批准译文；Terminology 提供审查过的词、认证、单位和分类。

Qwen-MT 只生成授权候选。Fact QA 守护数字/单位/型号/事实，Canonical QA 检查规则和展示。Owner Review 后生成 immutable patch，经 Apply 写 PRIMARY；Provenance 保留来源/批准/应用绑定。

STALE 表示绑定需复核，不等于必须重翻。旧中文仍正确可 REBIND；REBIND 只更新经验证的来源绑定，不改中文。目标不一致、open QA blocker、未审批时仍拒绝 metadata-only closure。

## Windows 自动任务

    powershell -ExecutionPolicy Bypass -File scripts/run_production_daily.ps1 -Date 2026-10-08 -ProjectRoot F:\ActionSKUTracker

官方脚本 scripts/run_production_daily.ps1；注册脚本 scripts/register_action_tracker_task.ps1。先核对参数与实际 Task Scheduler 状态，不能从配置文件推断已注册运行。Codex/ChatGPT 提醒与 Windows 任务调度器是两回事，本轮未注册或恢复任何任务。

## 故障恢复

| 情况 | 动作 |
| --- | --- |
| 采集失败、403/429/挑战页 | 保存证据并受控停止；无完整观测不推进缺失 |
| QA FAIL / Collection BLOCKED | 不提交事实；检查覆盖和质量证据 |
| Detail 失败 | 不否定有效 Presence；走来源绑定 detail-retry/detail-apply |
| Fact 成功、翻译失败 | 保留事实，只重试该 run 增量队列 |
| Registry FAILED/PENDING | Operations DEGRADED，精确 Registry 恢复，不重采重翻 |
| Approval Pending | 等 Owner 决定，不能自动批准 |
| Apply BLOCKED | 对照当前 head/hash、revision、QA blocker |
| Compatibility Export Pending | 同 run Resume 或 sync-exports；旧 head 投影会 superseded |
| Phase 3 BLOCKED | 查实际字段/数据缺口；Preview 不是正式发布，不降低 Gate |

    python -m action_tracker registry-retry --run-id <failed_collection_run_id> --commit-id <fact_commit_id> --date 2026-10-08
    python -m action_tracker production-run --date 2026-10-08 --resume --run-id <operations_run_id> --from-step REGISTRY
    python -m action_tracker sync-exports --commit-id <current_commit_id>
    python -m action_tracker db-validate-production

Registry retry 验证失败记录、commit/date/QA、冻结来源与当前事实。重复成功重试 ALREADY_READY；源变化拒绝旧 run。审计保留尝试次数和原因。单独恢复成功后 Resume 升级 Operations 状态。

备份使用 SQLite Backup API，不能直接复制正在写入 WAL 的主文件。恢复须先在副本验证 schema/integrity/foreign keys、兼容性和 Owner 授权，参见回滚指南。

## 安全、开发和测试

不绕过 Cloudflare/CAPTCHA；不修改历史只读目录；不以旧 Excel 覆盖 PRIMARY；不全量无差别 Qwen；不绕过 QA/Owner；不直接改 hash 清 blocker；不未经授权 reset/restore PRIMARY。

正式入口共用运行锁；事实计算前冻结 PRIMARY head，事务中核对 base；V2 不嵌套 collector 锁。默认 knowledge/localization production Apply、auto approval、AI 和 Spanish fallback 关闭，只有授权 profile 可以打开对应能力。

    python -m pytest -q

本地完整测试与 GitHub CI-safe 不同。CI 只跑 tests/ci_safe_tests.txt，临时文件/SQLite，不访问官网和模型。Ubuntu/Windows 必须是最终提交 SHA；真实访问、生产副本和生产验收分别留证，CI 不能替代来源/生产 QA。安全审批及本轮回归已纳入 CI，不静默 skip。

## 实现与授权状态

| 状态 | 能力 |
| --- | --- |
| 已实现 | daily Fact、Lifecycle/Price/Event、Registry/Queue、QA/审批/patch/Apply、严格发布、Resume、Preview 隔离、Registry 精确重试 |
| 须显式授权 | Provider、Owner 决定、生产 Apply、正式发布、计划任务、数据库恢复 |
| 实验/fixture | V2 临时独立事实写入、Fake Provider、高风险 fixture 自动审批、Scrapling shadow |
| 未自动化 | GitHub main protection 管理、未验证上一版自动回滚；无人值守 Owner 审批不属于目标 |

## 文档导航

- [当前状态](docs/CURRENT_STATE.md)、[架构](docs/ARCHITECTURE.md)、[Workflow V2](docs/WORKFLOW_V2_ARCHITECTURE.md)
- [SQLite](docs/SQLITE_PRODUCTION_STATUS.md)、[Translation](docs/TRANSLATION_SYSTEM_V1.md)、[Translation Contract](docs/TRANSLATION_SYSTEM_V1_CONTRACT.md)
- [数据模型](docs/DATA_MODEL.md)、[QA](docs/QA_RULES.md)、[Export](docs/EXPORT_ARCHITECTURE.md)、[Profile](docs/EXPORT_PROFILE.md)
- [Operations](docs/OPERATIONS_RUNBOOK_V2.md)、[Deployment](docs/WORKFLOW_V2_PRODUCTION_DEPLOYMENT.md)、[Rollback](docs/WORKFLOW_V2_PRODUCTION_ROLLBACK.md)、[AGENTS](AGENTS.md)
