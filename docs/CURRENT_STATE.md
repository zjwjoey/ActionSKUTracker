# Action SKU Tracker 当前状态

更新日期：2026-08-26

项目目录：`F:\ActionSKUTracker`

## 1. Git 与工作区

- 当前开发分支：`feat/export-dictionary-closure`。
- 本轮文档编写基线：`d8630ab`（同时是当时的 `main` / `origin/main`）。
- 工作区存在未提交的字典、增量审核和导出代码；这些修改不得与文档提交混在一起。
- 本文档提交只描述现状和目标，不代表本地未提交代码已经发布到远端。

## 2. 当前生产主链

正式日常链路仍是本地文件型系统：

```text
Sitemap / Listing / 补充入口
  ↓
Presence 冻结
  ↓
Lifecycle + QA
  ↓
Snapshot / Staging
  ↓ QA PASS + 非 dry-run
runtime/master/Action_Master.xlsx + runtime/state/*.csv
```

SQLite 位于 `src/action_tracker/database/`，仅是冻结脚手架，不参与日常读取、写入和正式提交。

## 3. 已稳定存在的能力

- Sitemap、15 个一级类目、Nuevo、Promoción semanal 采集；
- 不完整观测保护和 `UNKNOWN`；
- NEW、ACTIVE、MISSING_FIRST、MISSING_CONTINUED、OFFLINE、REAPPEARED；
- FIRST_SEEN 与 REAPPEARED 互斥；
- Presence 在 Detail 前冻结；
- Detail 中断后保留有效 Presence，并支持独立 retry/apply/backfill；
- QA FAIL / dry-run 不更新正式 Master 和跨日 State；
- Snapshot 使用 `runtime/snapshots/YYYY-MM-DD/<run_id>/`；
- 商品字典基线发布、哈希 manifest、构建审计与字段级人工覆盖。

Git 当前已发布字典基线：

| 文件 | 行数 |
| --- | ---: |
| 商品字典 | 8,617 |
| 品牌字典 | 509 |
| 类目关系 | 185 |
| 术语字典 | 33 |
| 人工覆盖 | 30 |
| 模型结果 | 825 |
| 源数据损坏记录 | 131 |

本地工作区正在审核的字典候选已扩展到品牌 588、术语 44，但尚未作为本轮代码/数据提交发布，不能把它们写成远端稳定基线。

## 4. 本地功能分支已实现但尚未单独提交的工作

以下功能已在本地 dirty worktree 中实现并通过测试，但代码尚未形成独立提交：

- STEP 1：每日 ES/ZH 无图 Export Profile；
- STEP 2：西班牙语无图正式导出；
- STEP 3：中文版无图字典 Join；
- STEP 4：`dictionary-enrich --run-id` 增量标准化；
- STEP 5：统一 `review-queue build/decide`；
- STEP 6：`term-candidates --run-id` 术语候选；
- 相关导出、审核队列、术语候选测试。

2026-08-26 在当前本地工作区执行完整测试：`190 passed`。

## 5. 导出模块状态

### 已有本地实现

- 一个 ES 无图 Excel；
- 一个 ZH 无图 Excel；
- 只读取 QA 通过、正式提交的 Master/Snapshot；
- ES/ZH SKU 集合与事实字段一致性校验；
- manifest、来源哈希和只读保护。

### 新冻结的 Template 1

目标改为一个 Excel、三张表：

1. `商品上下架明细`：历史 union + 每期 0/1 日期列；
2. `今日西班牙语清单`：当日有效 SKU、14 列、不插图；
3. `今日中文清单`：同一 SKU 集合、14 列、嵌入本地 250×250 白底图片。

2026-08-26 的示例有效数量为 5,476；这是当日校验示例，不是永久基线。Sitemap 数量不得替代正式有效 Listing/CURRENT 数量。

Template 1 目前只有文档契约，尚未完成三表合一、历史列更新、新 SKU union 和中文图片嵌入。`config/export_profiles.yaml` 和现有代码仍描述旧的两个独立无图输出，后续必须一起升级，不能把当前状态误写为已完成。

## 6. 历史 Presence（STEP 7）

已完成：

- `config/history_sources.yaml` 历史来源清单；
- 已识别日期：2026-01-09、04-05、07-01、07-06、07-13、07-20、07-27、08-02、08-10、08-24；
- 历史来源只读、SKU 去重和 Presence 不得反推的设计；
- Template 1 要求存在写 1、不存在写 0。

未完成：

- `export-history` 命令；
- 历史 union 构建服务；
- Template 1 第一张表；
- 历史 Presence manifest 和完整回归测试。

07-01 来源存在 5,903 行、5,511 个唯一 SKU，即 392 个重复行；构建 Presence 时必须按 SKU 集合去重，并在 manifest 保留原始行数和重复数。

## 7. Review 与术语状态

- Review Queue 使用稳定 `review_id` 去重，状态为 PENDING/APPROVED/REJECTED/RESOLVED；
- 人工决定按问题类型写入正确字典，不回写官网事实；
- 术语候选只从 NEW、source hash 变化和 NEEDS_REVIEW 增量提取；
- 候选不能自动晋升正式术语；
- 类目 scope 尚未进入正式术语 schema，不能静默新增。

## 8. 当前阻塞与发布条件

1. 本地未提交代码需要按功能分组审查和提交；
2. `config/export_profiles.yaml` 需要升级为 Template 1；
3. Template 1 需要实现和测试；
4. STEP 7 历史 Presence 服务尚未实现；
5. README/主文档完成后仍需代码、配置、测试交叉核对；
6. 正式上线前必须执行历史数据 dry-run、真实完整 run、QA PASS 和 export preview；
7. 图片下载模块继续独立，Export 只能消费已经存在的本地图片。

## 9. 下一步

按照 [ROADMAP](ROADMAP.md) 先审查并提交现有 STEP 1–6 本地代码，再实现可复用的历史 Presence 服务和 Template 1。SQLite 不参与这一阶段。
