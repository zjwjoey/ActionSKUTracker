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
- Saved View 运行页提供“保存当前 View 为 Selection”，Selection 会保存完整查询条件，
  并记录 `created_from_view_id`；View 删除后仅解除关联，Selection 快照继续保留。

## 真实 SQLite 快照

数据库 `F:\\ActionSKUTracker\\runtime\\db\\action_tracker.db` 当前包含 8,680 个
products：CURRENT 5,379、OFFLINE 650、MISSING 17、HISTORICAL 2,634；lifecycle
6,046 行；按 ACTIVE→CURRENT 映射后的 product/lifecycle mismatch 为 0；SQLite
integrity PASS，foreign-key check 为 0。该快照为只读验收记录。

真实 Extraction（source commit：`2026-08-31_2026-08-31_035941_114261d50c75`）：

| 查询 | 匹配数 | query_hash |
| --- | ---: | --- |
| CURRENT | 5,379 | `94137daa8e0e4ca4ea15a821e41a703ec360adfd3dea898e80dd2fcd6976e0bd` |
| NEW 最近 7 天 | 156 | `ec85c9827ba6e98e599cbd779b4b60b674d2137b683db99da8bfa607704d6b92` |
| PRICE_DOWN 最近 30 天 | 992 | `65015f13d48823a64336c015ce9abaa59cfe73aab8af683563d80a7054a28fb3` |
| PRICE_UP 最近 30 天 | 910 | `399d0763f45954ac4e1b25891df2a35d434f94e28355287bd0ae65c8d053cf44` |
| OFFLINE 最近 30 天 | 650 | `6262e53264c61b0ad75e5def040b935b15d3918488bdaa0e917e5c0280c996ce` |
| REAPPEARED | 426 | `430b7540112b77795c7481c7065a88e9ea33771d4ec09ee1d70ad9e91c18a2c4` |
| historical_low ≤ 2 | 2,325 | `442bda559d5a29e9d30002111dc0c6288b6fb3455775a3263020a61424645a80` |
| 中文六字段 COMPLETE | 5,361 | `0a4bdebcb9fef2ef71452b8db4c0040a5cc470c2ef388c6221e8da0465e31626` |

真实 Selection/Artifact 验收记录保存在
`F:\\ActionSKUTracker\\runtime\\temp\\v2_acceptance_20260831_final\\acceptance.json`：

- Selection ID：`sel_7b226eec2ccd`，匹配/成员 50，query hash
  `3b95be5df41e441efd917ca240428b468fbd7776db5da6d10808d7ef8b97c45f`，成员集合 hash
  `7386b259468bf2a508e431a922350a13faf35cc5f3041446c7cea36b7332dc11`。
- CSV Artifact：`artifact_ec7ab64249664e078dc396721fe1dda8`，50 行，缺失 0，SHA-256
  `6311bf07d2c2cdaf678fc40fe5c1e50bcce0fd9fcff4e3c799c2bbb88d265f85`。
- IMAGE_ZIP Artifact：`artifact_75957c525686462697c4d1d329e739d4`，50 张，缺失 0，
  SHA-256 `7f224288380aed8bd43f048fbffaab4dcefa6dc12e7f595ab09838dc659af74d`。
- 两个 Artifact 的 `source_commit_id` 和 `selection_source_commit_id` 均为上述 SQLite
  commit，CSV/ZIP manifest 路径彼此独立。
- 真实 ES Excel：5,379 行、5,379 个唯一 SKU、14 列，manifest SKU 集合一致，SHA-256
  `caaecc5ee5dbbcadb3610ac3c4d92991fcbba061fec477262094653808a21e4d`。

## 发布状态

本地完整回归在修复后为 341 passed。最终功能提交为 `07de5766a491b14e0fdeede5f334fae292273db3`；
其 GitHub Actions run `33360287371` 已通过 CI-safe 测试。验收文档记录提交为后续文档提交，
主分支不在本任务内自动合并，独立审查通过后才输出
`RECOMMEND MERGE`。

Windows Scheduler 的实际注册属于主机运维动作；仓库只提供注册脚本，未在代码中自动创建计划任务。
