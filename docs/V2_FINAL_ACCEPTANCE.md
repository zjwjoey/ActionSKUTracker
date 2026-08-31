# Architecture V2 Final Acceptance

本记录对应 `feat/action-data-platform-v2` 的最终收口。验收范围只覆盖
Extraction Contract、Selection、Artifact 和 localhost Workspace；不改变
Presence、Lifecycle、QA、Cloudflare 或 SQLite PRIMARY 写入边界。

## 验收门槛

1. SQLite integrity、foreign-key、生命周期对账通过。
2. Extraction 查询契约、Selection 完整成员语义、Artifact SKU 精确性通过。
3. 本地完整回归和同一 commit 的 CI-safe 回归通过。
4. Workspace 只能绑定 localhost，并覆盖 View/Selection/Artifact 基本流程。
5. 独立审查中 HIGH/MEDIUM 均为 0。

## 当前实现证据

- Extraction 默认 `CURRENT`，提供 canonical/SKU、价格、促销、生命周期、历史高低价、
  首次/最后确认/事件时间、图片和六字段中文完整性过滤。
- Selection 在创建时忽略展示层 `limit/offset`，固定完整匹配集合；CSV 和图片包保留成员
  缺失/不可用证据，不静默删行。
- Artifact 每次生成使用独立 ID，记录 `source_commit_id` 与
  `selection_source_commit_id`，并生成成员 manifest。
- Workspace 通过 `127.0.0.1` 提供查询、保存/运行 View、Selection 详情与导出、Artifact 历史。

## 真实 SQLite 快照

数据库 `F:\\ActionSKUTracker\\runtime\\db\\action_tracker.db` 当前包含 8,680 个
products：CURRENT 5,379、OFFLINE 650、MISSING 17、HISTORICAL 2,634；lifecycle
6,046 行；product/lifecycle mismatch 为 0；SQLite integrity PASS，foreign-key
check 为 0。该快照为只读验收记录。

## 发布状态

本地完整回归在修复后为 338 passed。最终修复提交
`b65a5bba73a825a2a80cfed79d34311f763f2a4e` 对应 GitHub Actions run
`33356239761`，CI-safe 测试 PASS。主分支不在本任务内自动合并，独立审查通过后才输出
`RECOMMEND MERGE`。

Windows Scheduler 的实际注册属于主机运维动作；仓库只提供注册脚本，未在代码中自动创建计划任务。
