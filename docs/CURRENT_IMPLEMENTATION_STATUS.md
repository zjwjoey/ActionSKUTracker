# Action SKU Tracker — 当前实现状态说明

更新日期：2026-08-10  
项目目录：`F:\ActionSKUTracker`

## 1. 项目目标

本项目用于长期监测 Action 西班牙站商品：识别 NEW、REAPPEARED、MISSING/OFFLINE、价格变化、促销/新品/可持续标签变化和内容变化。原则是本地优先，官网西语是事实来源，中文是派生数据，RAW Snapshot 保留，QA 未通过不得更新正式 CURRENT。

## 2. 当前运行状态

- Git 仓库已存在，当前分支为 `main`。
- 已有 64 个原项目测试，执行时全部通过。
- 新增 SQLite schema / 基线幂等导入测试共 2 项，均通过。
- 当前 CLI：`python -m action_tracker init-baseline`、`daily-run --dry-run`、`status`、`qa`。
- 当前 `status` 显示的旧文件型基线是 5,541 SKU；该数字与已确认的昨日基线 5,537 不一致，不能直接作为正式迁移输入。

## 3. 已实现功能（既有代码）

### 采集与监测

- sitemap 采集；分类 listing 扫描；Nuevo 与 Promoción semanal 专属入口扫描。
- 浏览器会话、失败/阻断记录；不绕过 CAPTCHA/Cloudflare。
- SKU Monitor 对 sitemap、listing、昨日 CURRENT 和历史 known SKU 做集合比较。
- 生命周期状态包括 NEW、ACTIVE、MISSING_FIRST、MISSING_CONTINUED、OFFLINE、REAPPEARED、ABSENT，以及 sitemap/listing 信号标记。
- 仅对 NEW、REAPPEARED、疑似价格/标签/内容变化、缺失字段等商品计划详情抓取；不是每日重抓全部详情。

### 变化与 QA

- 价格、内容和徽章 hash/比较模块。
- 促销、新品、可持续、折扣的标签解析模块。
- QA 包含 SKU 数量大幅变化、sitemap/listing 差异、价格异常、字段缺失等校验。
- dry-run 会保存 snapshot、staging、delta 和 QA 报告，但不应更新正式状态。

### Excel 与文件状态（旧实现）

- 当前实现使用 `runtime/master/Action_Master.xlsx` 作为日常基线，并导出/更新 Excel。
- 生命周期、翻译和图片映射当前仍主要保存在 `runtime/state/*.csv`。
- 这套文件型状态代码尚未被删除，以便迁移期间兼容和回退。

## 4. 已新增但冻结的 SQLite 层

已添加目录：`src/action_tracker/database/`

- `connection.py`：SQLite 连接、外键、WAL 模式。
- `schema.py`：创建以下表：
  - `products`
  - `product_observations`
  - `price_history`
  - `event_history`
  - `translations`
  - `image_map`
  - `runs`
  - `sync_queue`
  - `schema_migrations`
- `repository.py`：已有幂等 `import_baseline()`，将当前商品导入为指定观察日的 ACTIVE 产品和 baseline observation。
- 该目录按当前阶段要求保留但冻结；配置、baseline、daily-run 均不依赖它。

**重要：** SQLite 为未来预留，不参与当前正式链路。正式 daily-run 仍使用 CSV 状态文件与 Excel 临时文件替换。

## 5. 当前不符合目标规格的部分

1. SQLite 尚未成为唯一主数据库；CSV 仍是日常生命周期状态源。
2. 尚未实现统一 SQLite Writer 事务来原子写入 products、observations、price/event history、translations、runs 和 sync queue。
3. Excel 仍位于正式提交路径，而目标应为“SQLite 提交成功后由数据库导出 Excel”。
4. SQLite 基线导入不在当前阶段执行；正式基线仍由 Excel + CSV state 维护。
5. 当前运行目录中的历史 master/CSV 可能有 5,541 SKU；必须先确定正确的昨日基线文件，不能盲目覆盖。
6. 需要补充 SQLite Writer 的 rollback/事务失败测试、QA FAIL 不写库测试、数据库到 Excel 导出测试。

## 6. 基线规则（已澄清）

- 5,537 是昨天的 CURRENT SKU 数量，不是今天必须固定保留的数量。
- 它应作为昨日 ACTIVE 基线写入 SQLite，观察日期以对应 master 的最新有效日期为准。
- 今日采集的 sitemap + listing 交叉结果决定今日候选集。
- 今日数量可以增长或下降，但必须经过配置的 QA 阈值后才允许正式提交。
- 昨日存在、今日未发现：先 MISSING；达到 `offline_confirmation_runs`（当前配置 3）后才 OFFLINE。
- 今日出现且历史可靠记录从未出现：NEW。
- 今日出现、上一有效观察周期缺失、且更早出现过：REAPPEARED；FIRST_SEEN 当日绝不能同时 REAPPEARED。

## 7. 推荐的后续实施顺序

1. 确认并锁定正确的 5,537 SKU 昨日 Master 文件作为 SQLite baseline 输入。
2. 实现 `database/writer.py`：一个 SQLite transaction 统一写入所有正式数据表。
3. 将 `daily.py` 的 QA PASS 提交改为调用 Writer；QA FAIL / dry-run 只保留 snapshot 和 staging。
4. 将 `state.py` 的 CSV 读取替换为 repository 查询；CSV 仅作为一次性迁移/备份，不再是状态源。
5. 改造 Excel exporter：从 SQLite 的 products、price_history、event_history、runs、review queue 生成六个 Sheet。
6. 运行 dry-run 与完整回归测试后，再允许正式写入。

## 8. 建议与其他 AI 重点讨论的问题

- SQLite 表字段是否足以承载 Action 的完整事实与历史？是否应增加 category paths、促销起止时间、原始抓取证据 hash 等字段？
- 如何实现“SQLite 事务成功、Excel 导出失败”的恢复语义？建议数据库先提交，Excel 作为可重试派生物。
- 如何将现有 CSV 状态安全迁移到 SQLite，且不把错误的旧 REAPPEARED 事件带入正式 event_history？
- 如何以 sitemap/listing 两类证据控制 MISSING/OFFLINE，避免抓取失败引起批量下架？
- 如何把每日 raw snapshot、staging、QA 报告和正式 run 记录关联到同一 `run_id`？

## 9. 变更文件（本轮）

- `config/settings.yaml`
- `src/action_tracker/baseline.py`
- `src/action_tracker/database/__init__.py`
- `src/action_tracker/database/connection.py`
- `src/action_tracker/database/schema.py`
- `src/action_tracker/database/repository.py`
- `tests/test_database_schema.py`

本说明刻意不把未完成的 SQLite Writer / Excel-from-DB 导出描述为已实现。
