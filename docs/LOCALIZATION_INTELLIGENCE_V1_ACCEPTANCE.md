# Localization Intelligence V1 Final Closure

更新时间：2026-09-01。工作分支：`feat/chinese-localization-intelligence-v1`；main 基线：`a3cbb6bfb49431d7ddf4dc502d302e6efb44a3f2`。本轮只收口 Localization Intelligence，不改 Presence、Lifecycle、Price、PRIMARY 事实，也不执行正式 Apply。

## Code / Architecture

- Knowledge 状态合同统一为 `PENDING`、`AI_CANDIDATE`、`SEED_REVIEWED`、`HUMAN_REVIEWED`、`LOCKED`、`REJECTED`；正式加载只接受后三个可信状态。四个 V1 seed CSV 共迁移 14 行 `HUMAN_APPROVED → SEED_REVIEWED`。
- Review Queue 的 `APPROVED` 仍是任务状态，与 Knowledge 状态分离。
- 解析优先级实际落地为 Manual Override → Product Dictionary → 已确认 Brand/Category/Term/扩展 Knowledge → 同源可信 Model Cache → Deterministic → AI UNKNOWN → Review。
- `manual_by_sku`、同源可信 `model_by_sku`、字段级 `source_damage_by_sku` 已进入 `audit_current`；源损坏字段标记 `SOURCE_BLOCKED` 且不进入 AI。
- Promotion 对所有带 SKU evidence 的候选重新校验当前 PRIMARY 六字段 Localization source hash；CLI 不再硬编码 `source_hash_match=True`。
- `tests/test_localization_intelligence.py` 已加入 `tests/ci_safe_tests.txt`。

## Tests

- 专项：`27 passed`。
- 必测集合（Localization、Dictionary、Review Queue、Translation、DB Production、Post-merge safety）：`109 passed`。
- 全量：`390 passed`。
- Manual Override、Model Cache、Source Damage、Learning E2E、Promotion stale/pass contract 均有回归覆盖。

## Local Qwen3:8B

- Endpoint：本机 Ollama OpenAI-compatible `http://127.0.0.1:11434/v1`（仅临时烟测，未写入生产配置）。
- Model：`qwen3:8b`；health：`PASS`。
- 实际 smoke：`FAIL / LOCAL_QWEN_NOT_VERIFIED`。模型返回未包裹在合同要求的 `fields` 对象中，并出现英文/乱码；Validator 正确拒绝。数字保护烟测同样不得因此放宽。
- 生产默认仍为 `localization.ai.enabled=false`、`knowledge.production_apply_enabled=false`、`translation.auto_approval_enabled=false`，无密钥或本机绝对 endpoint 提交。

## CURRENT read-only audit

数据源：`F:\ActionSKUTracker\runtime\db\action_tracker.db`，run：`localization-v1-final-closure`；未写入 PRIMARY。

| 指标 | 实际值 |
| --- | ---: |
| CURRENT | 5,379 |
| READY | 389 |
| REVIEW_REQUIRED | 4,990 |
| 普通西语残留 | 1,671 |
| 数字事实 mismatch | 334 |
| FACT_NOT_COVERED | 0 |
| SOURCE_BLOCKED | 7 |
| SOURCE_HASH_CHANGED | 13 |
| STALE_LOCALIZATION | 13 |
| knowledge hit count / rate | 5,379 / 1.0 |
| AI eligible | 4,983 |
| AI calls / candidates | 0 / 0 |
| AI avoidance | 100% |

## CI / Git

- 本地 feature HEAD 在提交后记录；相对 `origin/main` 仅前进，不落后。
- 由于本轮环境无法连接 GitHub（fetch 返回代理连接失败），最新 feature HEAD 的 Ubuntu/Windows exact-head workflow、run ID 和 job 状态暂记 `NOT VERIFIED`；不得声称 CI PASS。
- 本轮不 merge main、不 force push；完成本地验证后再推送 feature 分支。

## 分层结论

- Code：`LOCALIZATION_V1_CODE_ACCEPTANCE_PENDING`（本地回归全部通过，但 exact-head CI 未验证，且 Qwen smoke 未通过）。
- Data：`LOCALIZATION_DATA_REVIEW_REQUIRED`。
- Production Apply：`NOT_READY / DISABLED`。
- Local AI：`LOCAL_QWEN_NOT_VERIFIED`。
- Recommendation：`DO NOT MERGE`，待 exact-head 双平台 CI 与本地模型合同 smoke 分别完成后再复核；任何情况下不得因 READY 数量不足而自动 Apply。
