# Action Localization Review Contract V2

这是两个 Action 中文本地化 Skill 共用的字段级合同。它定义候选生成、确定性检查、Codex 审核、Owner 队列和 Apply 边界，不替代生产代码中的 schema、数据库或 Apply contract。

## 阶段边界

```text
SOURCE SNAPSHOT → SOURCE HASH → deterministic fact extraction
→ dictionary/policy lookup → Qwen candidate generation
→ PRE-CODEX HARD QA → Codex SKU-context review
→ field-level decisions → cross-field consistency review
→ POST-CODEX HARD QA → risk routing → Owner queue
→ Owner approval → Apply Preview → hash gate → explicit Master Apply
```

Qwen 只生成候选中文；Codex 只负责语义审核和可靠修正。Codex 审核阶段不得调用 Qwen 或其他翻译提供方。

## 统一 Decision Contract

两个 Skill 只能使用以下四种 decision，并且每个 SKU 的每个字段独立决定：

- `KEEP`：候选忠实、完整、符合字段政策，且 reviewed value 等于 candidate value。
- `CORRECTED`：Codex 根据明确 source evidence 修正候选，且 `reviewed_value != candidate_value`。
- `REVIEW_REQUIRED`：source 冲突、身份/技术含义不明确、类目未映射、证据不足或修正需要猜测。
- `NO_SOURCE`：official source field 本身为空，且该字段不需要翻译。翻译失败或模型空输出不得标记为 `NO_SOURCE`。

## Risk Contract

`risk_level` 与 decision 独立，取值为 `LOW`、`MEDIUM`、`HIGH`。

- `HIGH`：产品身份、数字、数量、尺寸、容量、型号、接口、电压、功率、性别、适用对象、cat1/cat2 发生变化，或存在 source conflict。
- `MEDIUM`：受控术语、详情 key、技术 token 或分类映射需要确认。
- `LOW`：不改变事实的确定性格式或术语收口。

Owner queue 必须包含所有 `REVIEW_REQUIRED`、所有 `CORRECTED + HIGH`、未解决 hard QA 严重异常、source conflict 和 unknown/unmapped category。普通 `KEEP` 和 `CORRECTED + LOW` 不进入队列。

## Provenance 与 Hash Lineage

每个字段至少保留：

```text
sku, field, source_value, candidate_value, reviewed_value,
decision, risk_level, resolution_source, review_note,
source_hash, candidate_hash, reviewed_hash,
translator_model, translator_prompt_version,
review_model, review_policy_version
```

已有 `batch_id`、`run_id`、`source_snapshot`、provider call id、timestamp 等字段必须继续保留。

来源严格区分：`QWEN`、`CATEGORY_DICTIONARY`、`EXISTING_APPROVED_BASELINE`、`CODEX`、`OWNER`、`NO_SOURCE`。Codex 修改 Qwen 候选时，`candidate_source=QWEN`、`resolution_source=CODEX`。

Apply 前必须重新确认 source hash、current target hash、reviewed hash 和 policy manifest hash。source 变化时进入 `STALE_SOURCE`（或项目等价状态），禁止静默 Apply。

## Hard QA 与 Semantic QA

确定性 validator 只能 `PASS` 或 `FLAG`，不得翻译、改写、猜测或自动插入缺失事实。数字、单位、技术 token、字段为空、category dictionary、schema、details item count 和 hash 等机械检查优先复用现有 `translation/model_guard.py` 与项目验证器。

Codex 负责产品身份、语义、跨字段一致性和可靠修正。Codex 修正后必须再次执行 deterministic validation。

### Guard 与语义审核的硬边界

`Guard PASS IS NOT TRANSLATION PASS`。Guard 通过只表示确定性安全检查没有
发现问题，不能自动产生 `KEEP`，也不能因为 candidate 非空、没有 numeric
anomaly 或没有 Spanish residual 就跳过语义审核。审核人必须独立比较当前
目标字段的 `source_es` 与 `qwen_zh`，判断商品/属性身份、术语、事实增删、
数字/单位/数量/尺寸、型号/系列、品牌/IP/认证、否定、兼容性以及 details
的 key/value 语义。

每个字段的 primary source 是该字段自己的西语 source；其他字段只能作为
`CONTEXT FOR DISAMBIGUATION`。不得把 context 中目标 source 没有的事实带入
目标翻译。source 为空时目标必须为空并标记 `NO_SOURCE`，禁止跨字段 fallback。

品牌/IP 的 no-brand 展示规则只适用于 `name`。`description` 和 `details`
中已有的品牌、IP、系列、型号、标准、认证必须保留，禁止在自然语言翻译
之后执行通用品牌删除。

Cross-field review 比较：`name ↔ cat1 ↔ cat2 ↔ spec ↔ description ↔ details`。若 Spanish source 自身冲突，不得擅自选择真相，必须 `REVIEW_REQUIRED` + `SOURCE_CONFLICT`。

## Safety、Gold 与批次

两个 Skill 默认输出：`master_writes=0`、`production_apply=false`。只有 Owner 明确批准 Apply 后才能进入字段级 Apply Preview 和 rollback 流程。

Gold 候选至少要求 source provenance 完整、source hash 有效、candidate/reviewed lineage 完整、post-review hard QA PASS、无 unresolved source conflict、decision 不是 `REVIEW_REQUIRED`；High-risk correction 还必须 Owner approved。

批次审核继续使用 fixed-seed human sample。报告必须区分 `translation_provider_calls`、`review_provider_calls`、`qwen_calls_for_review`、`owner_review_completed`、`master_writes` 和 `production_apply`。Codex review 的 `qwen_calls_for_review` 必须为 `0`。
