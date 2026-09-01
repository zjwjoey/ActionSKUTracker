# Localization Intelligence V1 Final Closure

更新时间：2026-09-01。工作分支：`feat/chinese-localization-intelligence-v1`；main 基线：`a3cbb6bfb49431d7ddf4dc502d302e6efb44a3f2`。本轮只收口 Localization Intelligence，不改 Presence、Lifecycle、Price、PRIMARY 事实，也不执行正式 Apply。

## Code / Architecture

- Knowledge 状态合同统一为 `PENDING`、`AI_CANDIDATE`、`SEED_REVIEWED`、`HUMAN_REVIEWED`、`LOCKED`、`REJECTED`；正式加载只接受后三个可信状态。四个 V1 seed CSV 共迁移 14 行 `HUMAN_APPROVED → SEED_REVIEWED`。
- Review Queue 的 `APPROVED` 仍是任务状态，与 Knowledge 状态分离。
- 解析优先级实际落地为 Manual Override → Product Dictionary → 已确认 Brand/Category/Term/扩展 Knowledge → 同源可信 Model Cache → Deterministic → AI UNKNOWN → Review。
- `manual_by_sku`、同源可信 `model_by_sku`、字段级 `source_damage_by_sku` 已进入 `audit_current`；源损坏字段标记 `SOURCE_BLOCKED`，未损坏的未知字段仍可按字段进入 AI。
- Model Cache 只接受可信质量状态，并以当前 PRIMARY 的 legacy 四字段（name/cat1/cat2/spec）hash 匹配；不会使用过期 product-dictionary hash。
- Learning candidate 使用逐 SKU structured evidence；同一 SKU+hash 去重、同一 SKU 不同 hash 标记冲突。Promotion 对每一条 evidence 重新校验当前 PRIMARY 六字段 Localization source hash，结构化 evidence 损坏或缺失直接阻断。
- Manual Override 在最高优先级合并后再次运行最终 Validator；不合格人工值仍保持 `REVIEW_REQUIRED`。
- `tests/test_localization_intelligence.py` 已加入 `tests/ci_safe_tests.txt`。

## Tests

- 专项：`32 passed`。
- 必测集合（Localization、Dictionary、Review Queue、Translation、DB Production、Post-merge safety）：`114 passed`。
- 全量：`395 passed`。
- Manual Override、Model Cache、Source Damage、Learning E2E、Multi-SKU evidence、Promotion stale/pass contract、Qwen response contract 均有回归覆盖。

## Local Qwen3:8B

- Endpoint：本机 Ollama OpenAI-compatible `http://127.0.0.1:11434/v1`（仅临时烟测，未写入生产配置）。
- Model：`qwen3:8b`；health：`PASS`。
- 实际 product smoke：`PASS`；numeric/technical-token smoke：`PASS`。两次响应均为合同要求的 JSON envelope，SKU/source_hash/字段白名单、中文残留检查及数字/技术 Token 保留均通过。PowerShell 控制台可能以 GBK 显示中文为乱码，但响应原始 UTF-8 已通过解析与 Validator。
- 本次仅验证本地模型质量，不启用生产 AI，也不把 smoke 结果写入字典或 PRIMARY。
- 生产默认仍为 `localization.ai.enabled=false`、`knowledge.production_apply_enabled=false`、`translation.auto_approval_enabled=false`，无密钥或本机绝对 endpoint 提交。

## CURRENT read-only audit

数据源：`F:\ActionSKUTracker\runtime\db\action_tracker.db`，run：`localization-v1-final-final`；未写入 PRIMARY。

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
| AI eligible | 0（生产 AI 关闭） |
| AI calls / candidates | 0 / 0 |
| AI avoidance | 100% |

## CI / Git

- implementation feature HEAD：`cc4e2edb083b4834ec68a10eba62eb05eebdc7f2`。
- exact-head CI：run `33467132288`，head SHA 与 implementation HEAD 一致；Ubuntu `SUCCESS`、Windows `SUCCESS`，并确认 allowlist 实际执行 `tests/test_localization_intelligence.py`。
- 本轮不 merge main、不 force push；feature 分支已推送。

## 分层结论

- Code：本地回归 `PASS`；待本轮 implementation HEAD 的 exact-head 双平台 CI 完成后最终确认。
- Data：`LOCALIZATION_DATA_REVIEW_REQUIRED`。
- Production Apply：`NOT_READY / DISABLED`。
- Local AI：`LOCAL_QWEN_VERIFIED`（仅 smoke，不代表已训练或已进入生产）。
- Recommendation：等待本轮 implementation HEAD 的 exact-head 双平台 CI 后再决定是否合并；任何情况下不得因 READY 数量不足而自动 Apply。
