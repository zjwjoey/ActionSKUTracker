# Action SKU Tracker 当前状态

更新日期：2026-08-31
项目目录：`F:\\ActionSKUTracker`
当前开发分支：`feat/action-data-platform-v2`

## 当前结论

Architecture V2 的 Extraction、Saved View、Selection、Artifact 和本机
Workspace 闭环已在功能分支实现。当前工作区正在进行最终测试、真实 SQLite
只读验收和远端 CI 验证；主分支未修改，也不会自动合并。

## 生产数据边界

SQLite PRIMARY 是生产主链和唯一读事实源，Excel/CSV 只是兼容投影。主链保持：

```text
Sitemap / Listing / 补充入口 → Presence 冻结 → Lifecycle → QA → Snapshot/Staging
→ QA 通过且非 dry-run → SQLite PRIMARY → 兼容导出
```

本轮没有改动 Presence、Cloudflare、QA 或生命周期核心判断。

## SQLite PRIMARY 只读验收快照

数据库：`F:\\ActionSKUTracker\\runtime\\db\\action_tracker.db`

| 指标 | 数值 |
| --- | ---: |
| products | 8,680 |
| CURRENT | 5,379 |
| OFFLINE | 650 |
| MISSING | 17 |
| HISTORICAL | 2,634 |
| lifecycle rows | 6,046 |
| product/lifecycle mismatch | 0 |
| SQLite integrity | PASS |
| foreign_key_check | 0 |
| image_assets AVAILABLE | 5,396 |
| 中文 localization rows | 5,396 |

上述数据来自真实 SQLite；验收脚本只读数据库，不回写商品事实。

## V2 功能

- Extraction 默认 `CURRENT`，支持 SKU/canonical_id、生命周期、价格/促销、最近变价、
  历史低价/高价、首次/最后确认/事件时间、事件相对窗口、图片和六字段中文完整性。
- 价格方向优先读取 `price_history`；兼容旧批次在 `event_history` 中记录的
  `PRICE_UP/PRICE_DOWN`。`NEW` 映射 `FIRST_SEEN`/`ACTION_NEW_BADGE_ON`，`OFFLINE`
  使用生命周期离线日期。事件类型和日期约束在同一事件事实行上生效。
- Selection 忽略 UI 的 `limit/offset`，保存完整匹配成员；成员状态后来变为 OFFLINE
  时仍保留在 Selection 和 CSV 中。
- Artifact 使用独立生成 ID，保存 `source_commit_id` 与
  `selection_source_commit_id`，manifest 记录 requested/included/excluded 成员，
  不覆盖历史生成记录。
- Workspace 仅绑定 `127.0.0.1`，支持分页/排序、保存和运行 View、创建和查看 Selection、
  导出 CSV/Excel/图片包以及查看 Artifact 历史。

## 字典、图片与调度

字典 Apply、AI provider、Scoped Dictionary 和 Auto-Approval 仍按配置关闭；Resolver
和审核队列只在已授权路径运行。图片同步与 250×250 白底衍生图已实现，但不自动进入
daily 主链（`images.enabled=false`）。Windows 计划任务脚本已提供，主机是否注册属于
运维动作，当前保持 `READY_FOR_SHADOW`，不由代码自动注册。

## 测试与发布状态

- 本地完整回归：338 passed。
- CI-safe 白名单已包含 `tests/test_extraction_v2.py`、`tests/test_cli_v2.py`、
  `tests/test_operations_http_v2.py`。
- 最终提交 `dc67f3cde800b8f55aa44f7e9ae504d29c9bd4b7` 的 GitHub Actions run
  `33355330753` 已通过 CI-safe 测试；主分支仍未自动合并。
- 合并策略：完成独立审查且 HIGH/MEDIUM 均为 0 后，只给出 `RECOMMEND MERGE`，不自动合并。

## 相关文档

- `docs/ARCHITECTURE_V2.md`
- `docs/BOUNDARY_CONTRACTS_V2.md`
- `docs/EXTRACTION_CONTRACT_V1.md`
- `docs/DATA_WORKSPACE_V1.md`
- `docs/V2_FINAL_ACCEPTANCE.md`
- `docs/OPERATIONS_RUNBOOK_V2.md`
