# Action SKU Tracker 当前状态

更新日期：2026-09-01
项目目录：`F:\\ActionSKUTracker_main_merge`（验证 worktree；生产数据仍在 `F:\\ActionSKUTracker\\runtime`）
当前开发分支：`feat/chinese-localization-intelligence-v1`

## 当前结论

Architecture V2 已合并到 `main@59adcb1`。当前 feature 分支只收口
Localization Intelligence V1；不会回滚 V2，也不会修改真实商品事实。

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

- 本地完整回归：363 passed（热修复最终候选头）。
- CI-safe 白名单已包含 `tests/test_extraction_v2.py`、`tests/test_cli_v2.py`、
  `tests/test_operations_http_v2.py`、`tests/test_post_merge_production_safety.py`。
- `main@59adcb1` 的合并 CI 已通过；热修复分支已推送但尚未合并。
- 合并策略：完成独立审查且 HIGH/MEDIUM 均为 0 后，才给出
  `RECOMMEND MERGE HOTFIX TO MAIN`，不会自动合并。

## 相关文档

- `docs/ARCHITECTURE_V2.md`
- `docs/BOUNDARY_CONTRACTS_V2.md`
- `docs/EXTRACTION_CONTRACT_V1.md`
- `docs/DATA_WORKSPACE_V1.md`
- `docs/V2_FINAL_ACCEPTANCE.md`
- `docs/OPERATIONS_RUNBOOK_V2.md`

## Post-Merge Production Safety Hotfix

- Windows PID 探测统一使用 `services.runtime.is_process_alive()`；Windows 不再用
  `os.kill(pid, 0)`，RunLock 与 Operations Resume 使用同一实现。
- Listing 的 `goto`/reload 均由 BrowserSession + AccessController 统一控制；Listing
  不再直接刷新 Playwright page，也不会把 `goto=False` 当作成功。
- Resume 会恢复 allowlist 内的 delegated `run_id`、`commit_status`、`commit_id` 与 QA
  证据；导出待同步只重建当前 SQLite commit 的兼容投影，不重新采集或重复提交。
- `export_sync` 支持 `SUPERSEDED`，历史 commit 不再永久占用 pending 指标；Detail
  apply/backfill 在 SQLite PRIMARY 中仅改允许的详情事实，并再投影兼容 Excel/CSV。
- 真实库于 2026-08-31 只读验收：integrity PASS、foreign keys 0、CURRENT 5,379、
  MISSING 17、OFFLINE 650、HISTORICAL 2,634、lifecycle mismatch 0、export sync SUCCESS 8。
- Detail correction 已采用独立 correction run/commit：旧 parent commit 保持不可变，
  APPLY 要求 parent 是当前 HEAD，BACKFILL 仅填充空字段；西语事实变更会保留旧中文
  source_hash 并标记中文 freshness 为 `STALE`。历史价格低/高值覆盖 old/new/current
  全部观测端点。热修复最终提交及精确双平台 CI 结果以本分支最新 GitHub Actions
  记录为准。当前热修复代码修复提交为 `ee0ec7531e4bda9556801515f4a8cf29e2540cb8`；
  分支最新文档提交及其精确 HEAD CI 以 GitHub Actions 最新成功记录为准。
# Localization Intelligence V1 status

feature 分支新增统一 Localization Engine、语义/命名/规格规划、格式化、token-level Validator、AI provider、学习候选与 CLI 审计；生产 AI 和正式 localization apply 默认关闭，等待验收后再决定合并。

2026-09-01 收口状态：本地 Provider、结构化 Multi-SKU Learning/Promotion、Review Queue 字段适配、否定详情键和事实覆盖检查已实现；目标分支专项测试 32 passed、必测集合 114 passed、全量 399 passed。最新只读审计 run `local-qwen-policy-final-audit`：5,379 CURRENT、READY 394、REVIEW_REQUIRED 4,985、普通西语残留 1,671、数字事实 mismatch 334、FACT_NOT_COVERED 0、SOURCE_BLOCKED 7、SOURCE_HASH_CHANGED 13、STALE_LOCALIZATION 13、AI eligible 0（生产 AI 关闭）、AI calls 0、AI avoidance 100%。`c0abe02` 的 exact-head CI run `33482549398` 已通过 Ubuntu/Windows。代码层 `LOCALIZATION_V1_CODE_ACCEPTED`；数据层 `LOCALIZATION_DATA_REVIEW_REQUIRED`；Production Apply/Auto Approval 保持关闭。

此前本机 Ollama `qwen3:8b` endpoint health 与 product/numeric/technical-token JSON smoke 曾通过结构化合同和 Validator，记为历史 `LOCAL_QWEN_VERIFIED`，但没有写入生产；本次探测到未继承 F 盘模型目录的服务返回空模型列表，当前 endpoint 不重新记 PASS。新增本地训练提交已推送；`fa7824c` 的 exact-head CI run `33482383228` 已通过 Ubuntu/Windows，未改生产配置。

本地训练状态：`scripts/build_local_qwen_dataset.py` 已按 `NAMING_AND_SPEC_PLANNING_STANDARD_V1.0` 与 `CHINESE_LOCALIZATION_STANDARD_V1.0` 生成 364 条可信候选（333 train / 31 valid，147/16 个 SKU 组），每条样本带字段级 placement policy、数字/技术 Token 保护和源文档 hash。QLoRA 已在 F 盘本地 Qwen3:8B 上完成 1 epoch（RTX 3060，训练 loss 0.34097，验证 loss 0.06060）；适配器位于 `F:\LocalAI\Adapters\action-localization-qwen3-8b`，离线夹具验证 `all_pass=true`。规格原始模型输出中的半角 `|` 由既有确定性 `format_spec` 归一为 `｜` 后通过门禁。模型仍是离线候选，未写入字典、SQLite PRIMARY 或生产配置。
Ollama 的模型文件仍在 `F:\LocalAI\Models`；若通过 Ollama endpoint 使用，服务进程必须继承 `OLLAMA_MODELS=F:\LocalAI\Models`。本次只读探测到未继承该变量的服务返回空模型列表，因此不把当前 Ollama endpoint 记为新的 PASS；这不影响上述 HF/QLoRA 适配器验收。
# Field-contract hotfix status (2026-09-01)

Canonical mapping, source-damage blocking, post-override review rebuilding,
numeric protection, technical-token preservation and evidence-conflict
promotion blocking are implemented and covered by regression tests.  This is
code acceptance only; Production AI, automatic approval and localization
Apply remain disabled.  The current read-only audit remains data-review
required and does not modify PRIMARY.
