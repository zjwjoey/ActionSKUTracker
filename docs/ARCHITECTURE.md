# Action SKU Tracker 总体架构

## 1. 设计目标

系统长期回答四个问题：

1. Action ES 今天可以可靠确认哪些 SKU 在售？
2. 这些 SKU 相比上一有效观察发生了什么生命周期变化？
3. 官网西语事实如何被稳定、可审计地标准化为中文？
4. 如何生成固定、可复核的交付表，而不污染事实和 Master？

系统是本地优先的 Excel/CSV/JSON 管线，不依赖数据库作为生产主链。

## 2. 五层架构

```text
┌─────────────────────────────────────────────┐
│ 1. Collection                              │
│ Sitemap / Listing / Nuevo / Promoción      │
└──────────────────────┬──────────────────────┘
                       ▼
┌─────────────────────────────────────────────┐
│ 2. Presence & Lifecycle                    │
│ Evidence → CURRENT → NEW/MISSING/OFFLINE    │
└──────────────────────┬──────────────────────┘
                       ▼
┌─────────────────────────────────────────────┐
│ 3. QA & Formal State                       │
│ Snapshot / Staging / Master / State         │
└──────────────────┬───────────────┬──────────┘
                   ▼               ▼
┌────────────────────────┐  ┌─────────────────┐
│ 4. Dictionary & Review │  │ 5. Export       │
│ 中文、品牌、类目、术语 │  │ 固定交付模板    │
└────────────────────────┘  └─────────────────┘
```

## 3. Collection

主要入口：

- Sitemap：覆盖和 URL/SKU 发现证据；
- 15 个主类目 Listing：Presence 和列表字段事实；
- Nuevo、Promoción semanal：补充 Presence 与官方标签证据；
- Detail：名称、描述、详情、规格、图片等补充字段。

Sitemap 和 Listing 是不同证据。Sitemap-only 不能自动等同于有效商品行；Listing 不完整也不能直接把未见 SKU 判下架。

访问由 `services/access.py`、`services/browser.py` 控制。遇到 401/403/429/挑战页时遵循冷却、有限探测和停止规则，不绕过网站安全机制。

## 4. Presence 与 Lifecycle

Presence 必须在 Detail 开始前冻结。生命周期只消费冻结的有效 Presence：

- NEW：历史从未出现；
- ACTIVE：上一有效状态仍在售；
- MISSING_FIRST / MISSING_CONTINUED：有效观测中暂时缺失；
- OFFLINE：达到确认次数；
- REAPPEARED：历史存在、上一有效状态为 MISSING/OFFLINE、今天重新出现；
- UNKNOWN：观测不完整，不能推进状态。

同日重跑必须幂等。FIRST_SEEN 与 REAPPEARED 互斥。

## 5. QA、Snapshot 与正式提交

每轮使用唯一 `run_id`：

```text
runtime/snapshots/YYYY-MM-DD/<run_id>/
runtime/staging/<run_id>/
```

Snapshot 保存原始证据、标准化商品、覆盖率、Presence 来源、QA 和 run report。Staging 保存本轮准备提交的生命周期、价格、事件、翻译和商品变更。

提交分支：

```text
QA FAIL / dry-run
  └─ 保留 Snapshot、Staging、报告；不写 Master/State

QA PASS / PASS_PRESENCE_ONLY + 非 dry-run
  └─ 原子更新 Master 和跨日 State
```

Detail 完整性单独记录。Presence 完整且已冻结后，Detail 中断不否定当日 CURRENT。

## 6. Master 与 State

生产主数据仍由以下文件承担：

- `runtime/master/Action_Master.xlsx`：CURRENT、长期商品、事件、Review 和 run 记录；
- `runtime/state/known_skus.csv`：跨日生命周期事实；
- `runtime/state/offline_skus.csv`：由 known_skus 派生的 OFFLINE 视图；
- Snapshot/Staging：每轮可追溯证据。

SQLite 是未来预留层，当前冻结。任何 SQLite 迁移都必须单独设计事务、回滚、迁移和回归测试，不能混入字典或导出阶段。

## 7. Detail 补充链

Detail 不是主 Presence 链的一部分：

```text
正式 Observation
  ├─ 已有完整 Detail → 保留
  ├─ NEW / 变化 / 待补 → 进入有限 Detail 计划
  └─ 访问中断 → DETAIL_ACCESS_INTERRUPTED
                         ↓
                   detail-retry
                         ↓
                 detail-apply/backfill
```

详情补充必须保持父 observation 的 SKU 身份和来源证据，不得创建新的生命周期观察。

## 8. Dictionary 与 Review

字典是派生标准化层：

- 商品字典：SKU 级中文标准字段和来源哈希；
- 品牌字典：品牌标准名与别名；
- 类目字典：西语类目到 15 个中文一级类目；
- 术语字典：人工确认的标准词；
- 人工覆盖：同 SKU、同字段保护；
- 模型结果：只有 source hash 仍有效时可用；
- Review Queue：统一问题和人工闭环。

日常增量只处理 NEW、source hash 变化和 NEEDS_REVIEW。字典不决定价格、Presence 或生命周期。

解析与应用保持在主链之外：正式 Observation 通过后，`dictionary_resolver.py` 逐字段解析
并给出 `AUTO_READY`、`REVIEW_REQUIRED` 或 `SOURCE_BLOCKED`；`dictionary-coverage` 只读
统计，`dictionary-apply --dry-run` 只产出 preview、field_diff 和 manifest；正式 `--commit`
已有独立 QA/FULL_COMMIT/Audit、不可变事实和并发 hash Gate，但生产配置仍关闭，因此当前不允许该层直接写入
Master，避免字典异常影响 Presence 权威结果。

## 9. Export

Export 读取正式事实、字典、历史 Presence 和本地图片，生成交付文件。它不得访问官网或写回任何来源。

Template 1：

```text
一个工作簿
  ├─ 商品上下架明细      历史 union + 日期 0/1
  ├─ 今日西班牙语清单    当日有效 SKU，14 列，无图
  └─ 今日中文清单        同一 SKU，14 列，本地图片
```

历史 Presence 服务必须由 Template 1 和独立 `export-history` 复用，不能形成两套判断逻辑。

## 10. 代码边界

| 目录 | 职责 |
| --- | --- |
| `monitor/` | Sitemap、Listing、SKU 结构观察 |
| `services/lifecycle.py` | 单 SKU 生命周期分类 |
| `orchestrator/daily.py` | 日常编排，不承载所有业务细节 |
| `orchestrator/detail_retry.py` | 详情补充和应用 |
| `qa/` | QA 规则与结果 |
| `excel/` | Master 集中读写 |
| `dictionary.py` | 字典 schema、原子写入和字段保护 |
| `dictionary_enrichment.py` | 增量字典处理 |
| `review_queue.py` | 审核闭环 |
| `term_candidates.py` | 术语候选 |
| `exporting/` | 只读导出、校验和 Excel 写入 |
| `database/` | 冻结的 SQLite 脚手架 |

专题细节见 `LIFECYCLE_ARCHITECTURE.md`、`DICTIONARY_ARCHITECTURE.md` 和 `EXPORT_ARCHITECTURE.md`。

## 11. CI 与本地运行边界

GitHub Actions 只验证可重复的本地代码行为：schema 校验、生命周期规则、字典优先级、导出格式和审核队列等测试使用临时目录与模拟数据。CI 不访问 Action 官网、不启动真实浏览器、不读取或写入本机 runtime、不发布字典基线，也不下载图片。

真实 daily-run、详情补抓、QA 门禁、正式 Master/State 更新和导出预览仍必须在 Windows 本地按运行规则执行。CI 通过不能被解释为一次真实官网运行成功。
