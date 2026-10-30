# 临时任务文件与容量

生产启用遵循 GIT_SYNC_AND_RELEASE。数据根的现有正式入口使用版本化模板
`scripts/run_active_production.ps1`，严格校验active.json中的SHA、data_root和main/release分支身份，
再调用候选源码的原有daily入口。`-ValidateOnly`仅核对代码路由，不执行备份、采集或入库。
具体验收SHA、CI及启用状态保存在部署记录中，不能从代码存在推断已经切换。

## 存放约定

每次专项任务复用 P2 `ScratchTask`，将临时脚本、候选数据、调试输出放进同一目录：

```text
ActionTasks/
  .storage_candidate.json
  task-<owner-token>/
    owner.json
    lifecycle.jsonl
    scripts/      本次临时脚本
    data/         候选、检查点和中间结果
    debug/        必要的诊断输出
```

可复用的入库、QA、审批、备份功能继续放在版本管理的 `src/`、`scripts/`。
临时脚本调用这些现有功能，避免每批复制一套程序。新任务代码不散落在项目根或业务目录中。
正式来源、审批、Immutable Patch、Apply、恢复备份、Manifest 保持现有权威位置；
临时任务使用它们的路径/Hash引用，不额外复制整库或完整业务数据。
任务候选仍须通过原有正式入库链，任务完成不代表候选已入库。

`task-workspace` 是现有 `storage-foundation` 的薄 CLI 入口，不新增业务数据库或清理器。
默认预算128 MiB，登记时检查预计总量；文件写完后记录实际大小和SHA-256，超估会报警。
预算不拦截外部程序直接写入的每个字节，不是文件系统硬配额。
默认保留恢复义务和未知引用，不提供自动删除、移动历史文件或到期清理。

## 使用

在审查过的源码环境运行（已有Python及PYTHONPATH）：

```powershell
$task = python -m action_tracker storage-foundation task-workspace `
  --scratch-root F:/ActionStorageCandidates/ActionTasks `
  --owner-task localization-review --run-id review-20261023 --budget-mib 128 |
  ConvertFrom-Json

python -m action_tracker storage-foundation task-workspace `
  --scratch-root F:/ActionStorageCandidates/ActionTasks `
  --owner-task localization-review --run-id review-20261023 --task-dir $task.task_dir `
  --register data/candidates.jsonl --source reviewed-source --expected-bytes 1048576

# 让原有程序把本次候选输出到 $task.data/candidates.jsonl。
# 临时脚本用 scripts/登记；调试材料用 debug/登记。

python -m action_tracker storage-foundation task-workspace `
  --scratch-root F:/ActionStorageCandidates/ActionTasks `
  --owner-task localization-review --run-id review-20261023 --task-dir $task.task_dir `
  --complete data/candidates.jsonl
```

恢复同任务始终传回已返回的 `--task-dir`、同一owner/run；省略task-dir表示另开新任务。
再次登记相同来源/预算、完成相同Hash文件不追加日志；身份或已完成文件变化会阻断。
仅查看状态时传task-dir而省略register/complete；completed_bytes是登记完成量。
新来源版本或不同结果用新的文件名登记，避免一批覆盖另一批。

## 减少新产物与内存

- `listing_raw.json`流式写紧凑JSON；内容、字段、文件名和读取方式保留，小型报告仍缩进。
- 其它Snapshot JSON流式写入临时文件再原子替换，避免完整JSON字符串副本。
- Snapshot摘要按1 MiB读取，保留原有路径/零分隔/内容组合算法，旧文件摘要不变。
- Workflow V2预检只读16字节SQLite头；已有流式SHA校验继续运行。
- P2任务恢复逐行读日志，只保留每个资产的最新状态。
- 数据库验证备份复用P1的分块Hash和隔离恢复；同一任务Resume复用已验证产物。

优化只作用于新写入或新读取流程，不重写历史Snapshot或已保存Hash。
全量工作簿inspect输出仅在确有排障需要时生成；常规检查保存数量、差异与Hash，
避免为了查看几行反复输出完整商品数据。容量节省以独立实测为准。

CI测试仅使用生成数据和隔离目录，不访问官网、生产库、模型或云服务。

## 本地候选：生产入口集中暂存（尚未部署）

`transient_tasks.enabled: true` 时，现有 `run_production` 入口复用 P2 ScratchTask。
默认根目录 `F:/ai文件/CodexStorage/work/ActionTasks`，每次运行在一个 `task-<token>`
目录中保存 `scripts/`、`data/`、`debug/`。可用配置 `root` 或环境变量
`ACTION_TRACKER_TASK_ROOT` 选择其它同盘、非生产、非代码目录。`task-workspace`
省略 `--scratch-root` 时也使用这一默认值。缺少配置或显式关闭时保持原有适配行为。

每日正式报告目录的 `transient_workspace.json` 绑定 owner、run 和任务目录，
Resume 校验后继续使用同一目录。只把 `paths.temp` 暂存重定向到
`data/runtime_temp/`，本地化报告和学习候选使用显式权威路径，防止随 temp 漂移。
隔离 feed 的本地化报告仍在其隔离输出下。Master、PRIMARY、State、正式 Snapshot、
审批记录、备份和交付文件的位置均不变。原子发布临时文件仍可短暂位于目标同目录。

每次运行结束追加清单记录文件大小与 SHA-256，不复制暂存内容，不把上一版清单
重新嵌入新清单；容量超预算发出告警，不会删除文件或把已提交结果伪装成回滚。
恢复义务默认保留。256 MiB 是可配置任务预算而非对 Python 进程内存的硬上限。
本轮仅修改开发分支，生产配置和默认运行实际状态尚未切换。
默认值变更仅作用于新任务；未显式改 root 的 Resume 继续校验并复用原有 owner
记录指向的目录，不能自动搬动旧任务。新的测试缓存与临时文件使用
`F:/ai文件/CodexStorage/temp` 或同一任务的 data/debug 子目录。
已有 C 盘兼容联接保留；C 盘旧材料缺失时先核对 F 盘文件身份，不创建空替代物。

## F 盘旧文件整理方案

1. 新的一次性脚本、候选和排障输出从现在起只登记到对应任务目录，续跑复用目录。
2. 旧文件先建立只读身份清单，区分代码工作树、正式数据、可恢复证据和待确认文件。
3. 正式证据以路径及 Hash 引用复用，不为每批复制整库或全量表。
4. 旧文件迁移、压缩或清理另立明确名单；未知归属、唯一来源和未完成恢复验证的
   文件保留。本轮不搬动、不删除任何 F 盘历史文件。
