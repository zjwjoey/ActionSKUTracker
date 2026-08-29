# ActionSKUTracker 统一开发计划

更新日期：2026-08-30

本计划把两个方案合并管理：

1. **Export Foundation V1 正式发布**；
2. **SQLite Production Source of Truth 正式接管**。

“一起加入开发计划”表示两条主线纳入同一个最终交付目标，**不表示跳过中间门禁、把所有代码压成一个提交，或直接硬切生产**。每一阶段都必须可验证、可回滚。

## 一、最终目标

```text
Action 官网
  ↓
Collection / Presence / Lifecycle / QA
  ↓
CommitBundle
  ↓
SQLite Production Database
  ↓
Generated Outputs
  ├─ Action_Master.xlsx
  ├─ known_skus.csv
  ├─ offline_skus.csv
  ├─ ES/ZH Export
  ├─ Template 1
  └─ History Presence
```

最终角色固定为：

| 层 | 权威职责 |
|---|---|
| `runtime/snapshots/` | 保存原始采集证据、QA、Run Manifest |
| `runtime/db/action_tracker.db` | 保存正式商品事实、生命周期、观察、价格、事件和审核状态 |
| Excel/CSV | 生成式展示、兼容和交付文件 |
| Dictionary | 中文派生知识层，不改写官网西语事实 |

## 二、明确不纳入本轮

- 图片下载、图片标准化和带图 Excel；
- 新爬虫、Cloudflare 绕过、代理或指纹改造；
- Lifecycle 算法重写；
- 全量自动 AI 翻译；
- PostgreSQL、Web 后台、多机部署和 API Server；
- Dictionary 全面数据库化。

图片和自动化翻译在 SQLite PRIMARY 稳定后作为后续阶段，不塞进 Export V1 或 SQLite Cutover。

## 三、阶段总览

### Phase 0：稳定主链和版本基线

目标：冻结当前采集、Presence、Lifecycle、QA 语义。

- 记录 `origin/main`、工作区状态和完整测试基线；
- 不修改 `monitor/`、`services/lifecycle.py`、`orchestrator/daily.py` 核心判断；
- SQLite Mirror V1 单独封板，不与生产接管混合；
- 所有后续代码使用独立功能分支。

门禁：主链零业务行为变化，完整测试通过。

### Phase 1：Export Foundation V1 正式发布

正式能力：

1. ES 全量无图；
2. ZH 全量无图；
3. Template 1 无图三表；
4. 独立 History Presence Export；
5. Manifest、QA、只读保护、ES/ZH parity；
6. GitHub CI、合并 main、发布后真实验证。

#### 1.1 History Presence 收口

- Presence 使用 `1 / 0 / UNKNOWN` 三态；
- 每个 Seed/来源声明 `presence_capability`、`absence_capability`、`observation_complete`、`evidence_level`；
- 只有完整且具备 absence capability 的来源才能把未出现写成 `0`；
- 不完整来源的未出现必须写 `UNKNOWN`；
- Template 1 与独立 History 复用同一构建服务；
- 独立 History 增加“历史来源审计”工作表；
- History manifest 记录三态统计、来源 hash 和审计数量。

#### 1.2 Export V1 发布步骤

1. 从最新 `origin/main` 建立 `release/export-foundation-v1`；
2. 盘点本地 Export 增量，生成 `docs/export/EXPORT_RELEASE_DELTA.md`；
3. 补齐三态、来源能力、审计表和测试；
4. 生成四份真实文件并做结构、SKU、价格、链接和只读 hash 验证；
5. 完成 20 个 SKU 的业务抽样和 10 个跨期 SKU 的 History 抽样；
6. 推送分支并确认 GitHub CI 成功；
7. 合并 main；
8. 在 main 上重新生成四份文件并完成 Post-Merge Validation；
9. 标记 `Export Foundation V1 = RELEASED`。

门禁：`HIGH=0`、`MEDIUM=0`，ES/ZH/CURRENT 集合完全一致，History 不存在错误的 0，Master/State/Dictionary 未被导出修改。

### Phase 2：SQLite Production Contracts

目标：只冻结契约，不写生产接管逻辑。

新增/更新：

- `DATABASE_PRIMARY_ARCHITECTURE.md`；
- `DATABASE_PRIMARY_SCHEMA_V2.md`；
- `DATABASE_PRIMARY_COMMIT_CONTRACT.md`；
- `DATABASE_PRIMARY_WRITER_INVENTORY.md`；
- `DATABASE_PRIMARY_ACCEPTANCE.md`；
- `DATABASE_PRIMARY_CUTOVER.md`；
- `DATABASE_PRIMARY_ROLLBACK.md`。

必须盘点所有写入路径，并明确每条路径属于：

```text
A. 改成 SQLite Writer
B. 只保留为 Exporter
C. SQLITE_PRIMARY 模式禁止执行
```

冻结 `storage.mode`：

```text
EXCEL_PRIMARY → SQLITE_SHADOW → SQLITE_PRIMARY
```

门禁：所有正式 writer 都有唯一归属，不能存在未登记的第二真源。

### Phase 3：SQLite Schema V2、CommitBundle 和事务 Writer

新数据库身份：

```text
schema_family = ACTION_SQLITE_DATA
schema_version = 2.0.0
database_role = PRIMARY
```

核心表：

```text
products
product_localizations
lifecycle_state
observations
price_history
events
runs
reviews
commit_batches
run_evidence
export_sync
schema_metadata
source_records
migration_source_issues
```

实现：

- `CommitBundle`：把采集/计算结果与持久化解耦；
- `ProductionRepository` 和 `ProductionWriter`；
- `BEGIN IMMEDIATE`、WAL、foreign keys、FULL synchronous、busy timeout；
- `base_commit_id` 乐观提交门禁；
- append-only 价格和事件表；
- `event_key`、`price_event_key` 和 run 幂等；
- 事务失败只能 rollback，不产生 `PARTIAL_COMMIT`；
- `export_sync` 记录 DB 提交后 Excel/CSV 生成状态。

生产开关保持关闭，只运行 fixture 和故障注入测试。

### Phase 4：SQLITE_SHADOW

正式 daily-run 仍由 Excel/CSV 提交，同时使用同一个 `CommitBundle` 写入 Shadow SQLite。

连续至少 3 次真实正式 Run，对账：

- CURRENT SKU 集合；
- 商品事实；
- Lifecycle；
- Presence；
- 价格历史；
- 事件；
- Run；
- Review；
- known/offline 派生结果；
- 外键和孤儿记录。

Shadow 失败不得影响现有 Excel 生产主链。

门禁：连续 3 次 `0 mismatch`、`0 FK error`、`0 orphan`。

### Phase 5：SQLite Read Path

先切读路径，不切正式写路径：

```text
DB READ
Excel WRITE
```

替换并验证：

- `excel_reader.load_current()` → `repository.load_current_products()`；
- `state.load_known_skus()` → `repository.load_lifecycle_state()`；
- offline 由 `v_offline_skus` 派生。

用真实 dry-run 和 controlled run 验证 DB baseline 与原 Excel baseline 语义一致。

### Phase 6：SQLite PRIMARY Cutover Candidate

建立：

```text
runtime/db/staging/action_tracker_v2.staging.db
```

完成：

- products parity；
- lifecycle parity；
- observations/history parity；
- current parity；
- Export parity；
- Master/known/offline 可从 DB 重建；
- DB integrity、FK、backup restore 和 rollback 测试；
- 预提交备份和 30 个正式 Commit 保留策略。

执行 cutover backup：V1 DB、Master、known、offline、config、Git HEAD。

### Phase 7：SQLITE_PRIMARY

开启：

```yaml
storage:
  mode: SQLITE_PRIMARY
```

正式流程：

```text
Collection
→ QA
→ Snapshot
→ CommitBundle
→ SQLite Transaction
→ COMMIT
→ Master/State Generated Export
```

数据库提交成功后，Excel/CSV 导出失败不得回滚数据库，只能进入：

```text
DB_COMMITTED_EXPORT_PENDING
```

通过 `sync-exports` 重试。

必须增加：

- `db-status`；
- `sync-exports`；
- `db-validate-production`；
- SQLITE_PRIMARY 下 legacy direct writer 防护；
- DB integrity、base commit、export_sync、备份和恢复启动门禁。

### Phase 8：Compatibility View 降级

稳定后：

- `Action_Master.xlsx` 只能由 DB Exporter 生成；
- `known_skus.csv` 只能由 `v_known_skus` 生成；
- `offline_skus.csv` 只能由 `v_offline_skus` 生成；
- Excel/CSV 退出生产决策和写入职责，只保留展示、兼容和审计用途。

Dictionary Apply 必须写入 `product_localizations`，禁止继续直接改 Master。

## 四、SQLite PRIMARY 最终验收

必须全部通过：

- Schema、Integrity、Foreign Key；
- 事务 rollback；
- CURRENT exact parity；
- Lifecycle、Presence、Price、Events、Run、Review parity；
- same-day rerun 和 run_id 幂等；
- NEW / REAPPEARED 互斥；
- UNKNOWN 不制造 ABSENT/OFFLINE；
- Master/known/offline 可重建；
- DB commit 后 Export 失败可恢复；
- backup restore 和 production rollback；
- 所有 legacy writer 已迁移或禁止；
- Dictionary Apply 不产生双真源；
- Shadow 连续 3 次无差异；
- PRIMARY 真实 Run PASS；
- CI PASS；
- 文档全部同步。

## 五、最终执行顺序

```text
1. Export V1 History 三态和审计收口
2. Export V1 四份真实文件验收
3. GitHub CI、合并 main、Post-Merge Validation
4. SQLite Mirror V1 封板
5. Production Writer Inventory 和 Contracts
6. SQLite Schema V2 + CommitBundle + Transaction Writer
7. SQLITE_SHADOW 连续 3 次真实对账
8. SQLite Read Path
9. Cutover Candidate、备份、回滚和重建验证
10. SQLITE_PRIMARY
11. Excel/CSV 降级为 Generated View
12. 再做图片、Dictionary Apply 和自动翻译增强
```

## 六、每阶段分支和发布原则

- Export：`release/export-foundation-v1`；
- SQLite Contracts：`feat/sqlite-production-contracts`；
- SQLite Writer：`feat/sqlite-production-writer`；
- SQLite Read：`feat/sqlite-primary-read`；
- SQLite Cutover：`feat/sqlite-primary-cutover`。

不使用 `git add .`，不把 runtime、图片、报告、密钥或其他未相关修改混入功能提交。

每个阶段都执行：

```text
R0 自检
R1 独立代码审查
R2 真实数据验证
```

只有阶段门禁通过后，才允许进入下一阶段。
