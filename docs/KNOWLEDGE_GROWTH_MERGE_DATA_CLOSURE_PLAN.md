# Knowledge Growth Merge + Master/Dictionary/Export Closure V1

更新日期：2026-09-08

本文件是 `feat/master-dictionary-export-closure-v1` 的执行合同。它把已验收的 Knowledge Growth 合并和 Master/Dictionary/Export 数据闭环纳入统一计划，但不改变当前提取主链。

## 1. 安全边界

- 当前 `F:\ActionSKUTracker` 是活跃提取工作区；本计划所有操作均在 `F:\ActionSKUTracker_worktrees\` 的独立 worktree 完成。
- 不运行 daily-run、crawler、Playwright、scheduler、image-sync、Edge import 或任何会占用采集锁的任务。
- 不修改真实 PRIMARY、Master、State、Dictionary、runtime、浏览器 profile、锁文件和当前提取证据。
- 本阶段不执行 2026-09-07 历史成果迁移，不开启 Production Apply、AI、Auto Approval、Scoped Dictionary 或 Qwen 批量运行。

## 2. Knowledge Growth 合并

1. 记录实际远端 `main`、Knowledge Growth 和旧 Export 分支 SHA；
2. 确认 `origin/main` 是 Knowledge Growth 分支的 ancestor；
3. 确认 Knowledge Growth exact-head 的 Ubuntu/Windows CI 均成功；
4. 从 `origin/main` 建立 `merge/knowledge-growth-v1` worktree；
5. 只执行 `git merge --ff-only origin/feat/localization-knowledge-growth-v1`；
6. 只跑 Knowledge Growth 定向测试，记录 old main、feature HEAD、new main；
7. push `HEAD:main` 前再次检查远端 main 未前进；
8. 从新的 `origin/main` 建立 `feat/master-dictionary-export-closure-v1`。

旧 `feat/export-foundation-v1` 只能作为参考，禁止整分支 merge/rebase。`ff7421d1` Edge Detail Recovery 留到下一阶段。

## 3. Closure 实现顺序

### 3.1 现状盘点

逐模块标记：

```text
ALREADY_IMPLEMENTED
PARTIALLY_IMPLEMENTED
MISSING
SUPERSEDED
```

覆盖 field provenance、source hash、review/freshness、raw/normalized fact、patch lifecycle、Apply Gate、Release Gate、strict Export 和 category backlog。

### 3.2 字段级本地化

正式字段为 `name`、`cat1`、`cat2`、`spec`、`description`、`details`。每个字段独立保存 value、source、review_status、source_hash、updated_at、applied_commit_id、approved_by、approved_at 和 freshness_status。SKU 级 READY 只能由字段状态聚合，不能代替字段审批。

### 3.3 Raw / Normalized Fact

官网原始事实和机械标准化结果必须可追溯。双冒号、HTML、空白等格式清理只能生成 normalized 值，不得销毁 raw evidence；迁移必须 additive、幂等、向前兼容。

### 3.4 Immutable Patch 与 Apply Gate

每条 Patch 只覆盖一个 SKU 的一个字段，采用 `PATCH_CREATED → PATCH_APPROVED → PATCH_APPLIED` 或 `PATCH_REVOKED` 的追加事件。Apply 必须校验 source hash、字段审批、来源 allowlist 和当前官方西语事实；hash 变化自动转 REVIEW_REQUIRED。

### 3.5 Release / Export

`preview` 可保留明确标记的 FALLBACK_ES、PENDING、INCOMPLETE；`research_release` 禁止未批准 fallback、PENDING、UNREVIEWED、STALE、source-hash mismatch 和未解释西语残留。SQLite applied localization 是正式 ZH Export 唯一来源，正式 Export 不得现场拼接 Dictionary、Model Cache 或 Excel 临时修复。

Release Gate 至少要求：

```text
SKU_SET_MISMATCH = 0
FACT_MISMATCH = 0
UNDECLARED_DISPLAY_MISMATCH = 0
UNAPPROVED_ZH = 0
STALE_ZH = 0
SPANISH_RESIDUAL = 0
SOURCE_HASH_MISMATCH = 0
```

例外只能是有证据、审批人和过期时间的 `EXPLICIT_EXCEPTION`。

### 3.6 Category Backlog

`cat2` 缺失必须进入 `CATEGORY_MISSING` 或等价队列。关闭队列只能使用官方产品详情主面包屑/正式分类事实，禁止标题推断、常识推断、AI 常识推断和交叉陈列分类。

## 4. 测试与停止点

所有自动测试使用 temporary SQLite、仓库内 fixture 或 `tmp_path`，无网络、无浏览器、无模型、无生产路径。先跑 field provenance、patch、release gate、database production、dictionary apply 定向测试，再跑全量 pytest 和 Ubuntu/Windows CI。

完成标准：Knowledge Growth 已安全 fast-forward 到 main；Data Closure 从新 main 创建；闭环合同和测试通过；生产 Apply、AI、Auto Approval 仍关闭；历史迁移和 Edge Recovery 未启动。

本分支当前回归结果：`412 passed`。其中两个原有的“最近事件”测试已改为相对当前日期，避免固定历史日期导致日期滚动后的假失败；没有放宽生产查询语义。

## 5. 2026-09-08 数据修复审计结果

本次只读审计使用当前 PRIMARY 数据库和活跃工作区的本地候选字典，未写入生产数据库、Master 或 State：

| 项目 | 结果 |
|---|---:|
| CURRENT SKU | 5,547 |
| 中文 `cat2` 空值 | 320 |
| 西语 `cat2` 空值 | 4 |
| 有本地人工审核类目映射的中文候选 | 280 |
| 已用官方产品页主面包屑确认的西语候选 | 4 |
| 缺少正式中文类目映射、必须人工补齐 | 40 |

候选输出位于 `artifacts/data_repair_20260908/`。其中 `Action_Master_data_repair_candidate_20260908.xlsx`
只写入 280 条中文二级类目候选和 4 条官方西语二级类目候选，不能直接替代正式 Master；
`category_repair_candidates.json` 保存每个 SKU、字段、旧值、新值、来源和 Apply 状态。

当前远端 `main` 的类目字典仍有 186 条记录但 0 条中文 `cat2_zh`，而活跃工作区的本地候选字典有
77 条已填中文二级类目映射。本地候选字典必须先经过独立的 Dictionary/Field Apply 审批，不能直接进入
`research_release`。40 条缺映射记录保持 `BLOCKED/REVIEW_REQUIRED`，禁止按标题或常识自动翻译。

这 40 条已归并为 20 个类目对，并生成 `category_mapping_review_queue.csv`；队列中的中文只是模型辅助建议，
必须由人工确认后才能进入正式字典和下一次 Apply。

## 6. 2026-09-08 继续收口状态

Knowledge Growth 已在本地 Data Closure 工作分支完成合并，合并提交为
`9462aee`；合并后全量回归为 `414 passed`。这只是隔离工作树中的集成验证，
尚未推送或合并到远端 `main`，也未改变生产 SQLite、Master 或 State。

因此当前状态仍为：

- Knowledge Growth：`LOCALLY_INTEGRATED / NOT_RELEASED`；
- Data Closure：`CANDIDATE_ONLY`，候选修复尚未 Apply；
- Category Backlog：`REVIEW_REQUIRED`；
- Raw/Normalized Fact、Immutable Patch、Apply Gate：`IMPLEMENTED_IN_CONTRACT_TESTS_NOT_APPLIED_PRODUCTION`；
  Research Release Gate：`INTEGRATED_BUT_BLOCKED_BY_DATA`。

本地已增加只读 `research-release-audit` 命令、临时 SQLite 合同测试，并把
`export --research-release` 接入正式中文导出路径。当前门禁会在数据未完整审批时阻断发布；
它不会自动 Apply 或修改生产数据。

Raw/Normalized Fact 现在保留 `raw_value` 与 `normalized_value` 的不可变版本；
Localization Patch 采用 `PATCH_CREATED → PATCH_APPROVED → PATCH_APPLIED/REVOKED`
追加事件，并有 source hash、字段和来源 allowlist 的 Apply Gate。上述能力已在临时
SQLite 通过合同测试，但按本阶段安全边界尚未迁移或写入真实 PRIMARY。

`localization_field_provenance` 也已加入 SQLite V2 additive schema，并由后续
ProductionWriter 提交同步六个字段的独立来源、审批状态、source hash、freshness
和 applied commit；现有 PRIMARY 尚未执行迁移，因此当前生产数据仍按旧投影审计。

Repository 读取路径和 `research_release` 门禁已优先读取该字段级投影；旧数据库缺少
该表时只走兼容回退，并保留明确的全局状态检查。

`category_backlog` 与事件表已加入 additive schema；关闭 `CATEGORY_MISSING` 必须提供
官方商品页证据 URL、人工决策人和中文值，不能使用标题、常识或交叉分类自动关闭。
