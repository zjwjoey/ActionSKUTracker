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
- 全量：`399 passed`（含离线 Qwen 训练集生成器、QLoRA 数据门禁和适配器评估脚本回归）。
- Manual Override、Model Cache、Source Damage、Learning E2E、Multi-SKU evidence、Promotion stale/pass contract、Qwen response contract 均有回归覆盖。

## Local Qwen3:8B

- Endpoint：本机 Ollama OpenAI-compatible `http://127.0.0.1:11434/v1`（仅临时烟测，未写入生产配置）。
- Model：`qwen3:8b`；health：`PASS`。
- 实际 product smoke：`PASS`；numeric/technical-token smoke：`PASS`。两次响应均为合同要求的 JSON envelope，SKU/source_hash/字段白名单、中文残留检查及数字/技术 Token 保留均通过。PowerShell 控制台可能以 GBK 显示中文为乱码，但响应原始 UTF-8 已通过解析与 Validator。
- 本次完成了独立的本地 Qwen3:8B QLoRA 训练：364 条可信样本（333 train / 31 valid），
  使用 `NAMING_AND_SPEC_PLANNING_STANDARD_V1.0` 的字段级 placement policy，训练 1 epoch，
  train loss 0.34097、eval loss 0.06060。适配器和 `training_manifest.json` 保存在
  `F:\LocalAI\Adapters\action-localization-qwen3-8b`；固定 product 与 numeric/technical-token
  夹具评估 `all_pass=true`。规格原始候选的半角 `|` 经既有 `format_spec` 确定性归一为 `｜` 后通过 Validator。
- 适配器仍是离线候选，不启用生产 AI，也不把训练或评估结果写入字典或 PRIMARY。
- 生产默认仍为 `localization.ai.enabled=false`、`knowledge.production_apply_enabled=false`、`translation.auto_approval_enabled=false`，无密钥或本机绝对 endpoint 提交。

## CURRENT read-only audit

数据源：`F:\ActionSKUTracker\runtime\db\action_tracker.db`，run：`local-qwen-policy-final-audit`；未写入 PRIMARY。

| 指标 | 实际值 |
| --- | ---: |
| CURRENT | 5,379 |
| READY | 394 |
| REVIEW_REQUIRED | 4,985 |
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

- implementation feature HEAD：`c0abe02`（本地 Qwen 训练、格式门禁与最终只读审计文档收口）。
- exact-head CI：run `33482383228`，head SHA 与 implementation HEAD 一致；Ubuntu `SUCCESS`、Windows `SUCCESS`，并确认 allowlist 实际执行 `tests/test_localization_intelligence.py`。
- 本轮不 merge main、不 force push；feature 分支已推送。

## 分层结论

- Code：本地回归 `PASS`；`c0abe02` 的 exact-head 双平台 CI run `33482549398` 已完成，Ubuntu/Windows 均 `SUCCESS`。
- Data：`LOCALIZATION_DATA_REVIEW_REQUIRED`。
- Production Apply：`NOT_READY / DISABLED`。
- Local AI：`QWEN3_8B_SMOKE_PASS`；`QLORA_ADAPTER_OFFLINE_EVALUATION_PASS`（不代表已进入生产）。
- Final code conclusion：`LOCALIZATION_V1_CODE_ACCEPTED`；`RECOMMEND MERGE FEATURE TO MAIN`（仅建议，不自动合并）。
- Data/Production conclusion：`LOCALIZATION_DATA_REVIEW_REQUIRED`；`PRODUCTION_LOCALIZATION_APPLY_NOT_READY`；AI、Apply、Auto Approval 均保持关闭。
# Final field-contract hotfix acceptance (2026-09-01)

Status: `LOCALIZATION_V1_CODE_ACCEPTED` after the canonical field contract,
description source-damage gate, numeric and technical-token guards, manual
override reason rebuild, and `EVIDENCE_CONFLICT` promotion block were added.
The full regression suite passed 406 tests; the hotfix/knowledge targeted set
passed 148 tests.  Current audit is read-only (`5379 CURRENT`, `394 READY`,
`4985 REVIEW_REQUIRED`, `1671 SPANISH_RESIDUAL`, `334 NUMERIC_MISMATCH`,
`7 SOURCE_BLOCKED`, knowledge hit rate `1.0`, AI calls `0`).

Production safety remains unchanged: Localization AI OFF, Production Apply
OFF, Auto Approval OFF, PRIMARY untouched.  The feature branch is not merged
to `main` by this acceptance.

Hotfix exact-head evidence: feature HEAD `7bdb1a631c1b560f60251190f717006a1fff2a63`;
CI Run `33487876060` ran on that exact head and both Ubuntu and Windows jobs
finished successfully.

Final merge-gate closure: Manual Override is a terminal SKU × Field authority
(`source=manual_override`, `status=READY`) while remaining subject to
Validator fact/safety checks; it is excluded from AI requested fields and does
not trigger a synthetic `PRODUCT_TYPE_REVIEW`.  `EVIDENCE_CONFLICT` is
rejected independently by both `can_promote()` and
`KnowledgePromotionRouter` before freshness or file staging.  Hyphenated
ordinary Spanish tokens such as `anti-edad` are not treated as mandatory
technical tokens.

Final merge-gate audit (read-only, run
`localization-v1-final-merge-gate`): `5379 CURRENT`, `396 READY`,
`4983 REVIEW_REQUIRED`, `1671 SPANISH_RESIDUAL`, `334 NUMERIC_MISMATCH`,
`0 FACT_NOT_COVERED`, `7 SOURCE_BLOCKED`, `13 SOURCE_HASH_CHANGED`,
`13 STALE_LOCALIZATION`, knowledge hit rate `1.0`, AI calls `0`.
Final pushed HEAD is `4988d1f6bae416a46543ec65fd0b62c8bd205ea8`; exact-head
CI Run `33490269220` passed on Ubuntu and Windows.
