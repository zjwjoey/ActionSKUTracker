# 商品中文标准化智能模块架构 V1.0

> 建议 GitHub 路径：`docs/LOCALIZATION_INTELLIGENCE_ARCHITECTURE.md`
>
> 适用项目：ActionSKUTracker
>
> 模块英文名：Chinese Product Localization & Normalization Intelligence
>
> 模块简称：Localization Intelligence

## 1. 模块定位

该模块不是“西语翻译器”，而是 Action 商品中文字段的统一语义规划、标准化、AI 补全、验证和学习系统。

它负责七个正式中文字段：

- 中文品名 `name_zh`
- 中文分类1 `cat1_zh`
- 中文分类2 `cat2_zh`
- 中文规格 `spec_zh`
- 中文单价 `unit_price_zh`
- 中文描述 `desc_zh`
- 中文产品详情 `details_zh`

最终链路：

```text
西语官网事实 / SQLite PRIMARY
        ↓
Fact Parser（事实拆解）
        ↓
Semantic Classifier（语义角色识别）
        ↓
Field Planner（字段规划）
        ├─ Naming Planner
        ├─ Spec Planner
        ├─ Category Planner
        ├─ Unit Price Planner
        ├─ Description Planner
        └─ Detail Planner
        ↓
Knowledge Resolver（现有知识命中）
        ↓
UNKNOWN only
        ↓
AI Resolver（只解决未知部分）
        ↓
Deterministic Formatter
        ↓
Validator / Policy Gate
        ↓
标准中文候选
        ↓
Learning Candidate
        ↓
Review / Evidence / Promotion
        ↓
正式 Knowledge Base
        ↓
下一次直接命中
```

核心原则：

> AI 负责探索；字典负责记忆；规则负责约束；Planner 负责字段归属；Validator 负责事实保护；人工/证据晋升负责长期纠错。

“学习”是结构化知识积累，不是在线微调模型，也不是把每一次 AI 答案无条件写入正式字典。

## 2. 与现有模块的关系

必须复用并整合现有能力，而不是另建平行系统：

- `action_tracker.dictionary`
- `dictionary_resolver.py`
- `dictionary_enrichment.py`
- `review_queue.py`
- `translation/`
- `exporting.dictionary_join`
- `data/dictionary/`
- `config/dictionary_terms.yaml`
- `docs/DICTIONARY_ARCHITECTURE.md`
- `docs/DICTIONARY_ENRICHMENT.md`
- `docs/AI_TRANSLATION_CONTRACT.md`
- `docs/EXPORT_PROFILE.md`

旧公共接口保留兼容 wrapper；最终只能有一套正式解析规则。

禁止形成：

```text
Export一套中文逻辑
Dictionary一套中文逻辑
AI一套中文逻辑
Workspace一套中文逻辑
```

所有调用必须收敛到统一 `LocalizationEngine` / `LocalizationResolver`。

## 3. 建议代码结构

```text
src/action_tracker/localization/
    __init__.py
    engine.py
    contracts.py
    context.py
    policy.py
    semantic.py

    planning/
        naming.py
        spec.py
        category.py
        description.py
        detail.py

    formatters/
        naming.py
        spec.py
        unit_price.py
        description.py
        detail.py

    validators/
        spanish.py
        numeric.py
        identity.py
        brand.py
        category.py
        spec.py
        detail.py

    ai/
        provider.py
        disabled.py
        openai_compatible.py
        prompts.py
        schemas.py
        resolver.py

    knowledge/
        loader.py
        contracts.py
        indexes.py

    learning/
        candidates.py
        evidence.py
        promoter.py
        manifest.py
```

目录可以根据现有代码压缩，但职责必须保留。

## 4. 三种核心对象

### 4.1 SourceFacts

只保存官网/PRIMARY 正式事实：

```text
sku
canonical_id
name_es
cat1_es
cat2_es
spec_es
unit_price
current_price
original_price
desc_es
details_es
product_url
image_url
source_hash
source_run_id
source_commit_id
```

AI、字典和中文结果不得反向修改这些事实。

### 4.2 SemanticFacts

Fact Parser 和 AI Resolver 将源事实拆解为语义项：

```text
PRODUCT_TYPE
BRAND
SERIES
MODEL
TECH_TOKEN
STANDARD_UNIT
SIZE
CAPACITY
WEIGHT
QUANTITY
COLOR
VARIANT
MATERIAL
FUNCTION
COMPATIBILITY
ELECTRICAL
CARE
NUTRITION
DETAIL_KEY
DESCRIPTION_FACT
```

每个语义项至少记录：

```text
source_text
normalized_source
zh_value
semantic_type
confidence
knowledge_source
allowed_targets
preferred_target
keep_original
source_hash
```

### 4.3 LocalizationPlan

Planner 决定语义事实应该落到哪里：

```text
name_tokens
spec_tokens
cat1
cat2
description_facts
detail_pairs
unit_price
review_reasons
```

Planner 不得删除源事实；被排除在品名/规格之外的重要事实应进入描述、详情或 Review Evidence。

## 5. Field Planner 是模块核心

翻译和字段规划必须分离。

例如：

```text
Lámpara LED de mesa recargable
220 V | 10 W | varios colores
```

语义拆解：

```text
Lámpara de mesa → PRODUCT_TYPE → 台灯
LED → TECH_TOKEN → 原样保留
recargable → FUNCTION → 可充电
220 V → ELECTRICAL → 220V
10 W → ELECTRICAL → 10W
varios colores → COLOR_VARIANT → 多种颜色
```

字段规划：

```text
品名：可充电LED台灯
规格：220V｜10W｜多种颜色
```

不得机械得到：

```text
可充电多色220V 10W LED台灯
```

## 6. 品名规划与规格规划必须去重

同一语义事实不得机械重复进入多个展示字段。

例如：

```text
品名：微纤维清洁布
规格：3件｜50×60cm｜多种颜色
```

如果“微纤维”已经决定核心商品类型，则规格不再重复“微纤维”。

又如：

```text
品名：LED灯泡
规格：E27｜10W｜220–240V
```

技术词是否进入品名由“是否决定商品身份”判断；普通数值参数优先进入规格。

## 7. 知识层

继续保留：

```text
data/dictionary/product_dictionary.csv
data/dictionary/brand_dictionary.csv
data/dictionary/category_dictionary.csv
data/dictionary/term_dictionary.csv
data/dictionary/manual_overrides.csv
data/dictionary/model_translation_overrides.csv
data/dictionary/source_damage_report.csv
```

新增：

```text
product_type_dictionary.csv
detail_key_dictionary.csv
tech_token_dictionary.csv
phrase_dictionary.csv
```

### 7.1 Product Type Dictionary

回答：

> “这一类商品统一叫什么？”

示例：

```text
paño de microfibra → 微纤维清洁布
lámpara de mesa → 台灯
caja de almacenamiento → 收纳箱
alfombrilla para cortar → 切割垫
```

### 7.2 Detail Key Dictionary

回答：

> “官网详情字段名标准中文叫什么？”

例如：

```text
Color → 颜色
Material → 材质
Potencia → 功率
Voltaje → 电压
Número del artículo → 商品编号
```

### 7.3 Tech Token Dictionary

记录必须保留原文或标准形式的技术/型号 Token：

```text
LED
USB-C
HDMI
Bluetooth
Wi-Fi
IP44
E27
GU10
ABS
PVC
FSC
GRS
4K
1080p
```

“全大写”不能自动等于技术词；必须来自知识库、严格规则或人工确认。

### 7.4 Phrase Dictionary

保存高频短语与语义角色：

```text
varios colores → 多种颜色 → SPEC_ATTRIBUTE
varios modelos → 多款可选 → SPEC_ATTRIBUTE
apto para lavavajillas → 可用洗碗机清洗 → DETAIL_ATTRIBUTE
```

## 8. 普通西班牙语零容忍

正式中文字段中普通西班牙语必须为 0。

允许保留的拉丁/数字 Token 必须属于：

```text
BRAND_TOKEN
SERIES_TOKEN（有证据）
TECH_TOKEN
MODEL_TOKEN
STANDARD_UNIT
STANDARD / CERTIFICATION CODE
```

例如以下合法：

```text
3M牌胶带
Capetown系列毛巾（仅系列身份已确认时）
LED灯泡
USB-C数据线
IP44
E27
ACEA A3/B3
100g
50ml
220V
10W
```

普通西语：

```text
colores
varios
plástico
recargable
incluye
unidades
```

必须中文化。

Validator 必须做 token-level 检测，不能继续采用“字段里只要有中文就忽略全部拉丁词”的粗粒度规则。

## 9. AI Resolver

AI 只处理 UNKNOWN，不重翻已确认知识。

输入至少包含：

```text
source facts
cat1/cat2 context
已命中的品牌/产品类型/术语/技术 Token
当前 Policy Version
相似正式知识（限制数量）
source_hash
```

输出必须为结构化 JSON，禁止自由文本作为正式接口。

建议 schema：

```json
{
  "product_type_candidate": {
    "source": "...",
    "zh": "...",
    "confidence": 0.0
  },
  "semantic_items": [
    {
      "source": "...",
      "zh": "...",
      "semantic_type": "FUNCTION",
      "preferred_target": "name",
      "keep_original": false,
      "confidence": 0.0
    }
  ],
  "detail_key_candidates": [],
  "review_notes": []
}
```

AI 不负责最终数字、URL、SKU、价格和 commit provenance。

## 10. AI Provider

实现 Provider Interface。

至少提供：

```text
DisabledProvider
FakeProvider（测试）
OpenAICompatibleProvider（运行时配置）
```

配置不得硬编码密钥：

```text
ACTION_AI_API_KEY
```

建议配置：

```yaml
localization:
  policy_version: CHINESE_LOCALIZATION_STANDARD_V1
  ai:
    enabled: false
    provider: openai_compatible
    base_url: null
    model: null
    api_key_env: ACTION_AI_API_KEY
    temperature: 0
    max_batch_size: 20
    max_retries: 3
```

模块交付必须完成 AI Adapter，但生产默认仍可保持 `enabled=false`，直到用户配置真实 provider/model/key。

## 11. Validator

AI 和规则结果必须经过统一 Validator：

- SKU identity preservation
- URL preservation
- price preservation
- numeric preservation
- ordinary Spanish residue
- brand evidence
- category fixed set
- category mapping uniqueness
- spec grammar
- detail key whitelist / candidate gate
- details 商品编号 == SKU
- source_hash consistency
- manual lock preservation
- localization freshness/provenance

任何事实级失败不得自动 Apply。

## 12. Learning Engine

新知识进入 Candidate Pool：

```text
runtime/localization/learning_candidates/
```

每条 Candidate 至少包含：

```text
candidate_id
knowledge_type
source_text
normalized_source
zh_candidate
semantic_type
preferred_target
allowed_targets
category_context
evidence_skus
occurrence_count
provider
model
prompt_version
policy_version
confidence
validator_status
review_status
created_at
updated_at
```

## 13. 学习等级

```text
L0 UNKNOWN
L1 AI_CANDIDATE
L2 EVIDENCE_ACCUMULATED
L3 HUMAN_REVIEWED
L4 LOCKED
```

AI 单次高置信结果不得直接变成 `HUMAN_REVIEWED` 或 `LOCKED`。

可以自动晋升的低风险知识仅限严格确定性类型，例如：

- 标准计量单位
- 明确格式可验证的技术代码
- 已存在词的大小写/空格归一化

品牌、商品类型、分类、语义翻译不得仅凭单次模型输出自动成为正式知识。

## 14. Promotion Gate

正式晋升必须：

```text
schema PASS
validator PASS
source_hash/evidence 可追溯
无冲突正式知识
无人工 LOCK 冲突
满足该 knowledge_type 的 promotion policy
```

晋升记录必须写 manifest：

```text
candidate_id
old_state
new_state
reviewer / policy
reason
source evidence
knowledge file before hash
knowledge file after hash
```

## 15. 与 SQLite PRIMARY 的关系

正式中文事实属于 PRIMARY 的 versioned fact projection。

禁止：

```text
直接 UPDATE 当前 PRIMARY
且保持同一个旧 commit_id
```

正式 `localization-apply --commit` 必须：

```text
C1 current HEAD
↓
Localization Apply
↓
C2 LOCALIZATION_CORRECTION
base_commit_id = C1
↓
C1 immutable
C2 becomes HEAD
```

只允许修改中文 localization / provenance 和必要 localization event；不得修改价格、Presence、Lifecycle、西语事实。

新 correction commit 必须遵守现有 export_sync supersede invariant。

## 16. Freshness / Provenance

普通 daily-run 不得无条件重建中文 metadata。

必须保留：

```text
review_status
freshness_status
source_hash
resolution_status
approved_by
approved_at
field sources
last_commit_id
applied_commit_id
```

语义：

```text
西语 source hash 未变
→ 中文 provenance 原样保留

西语 source hash 变化、中文未重新生成
→ 中文内容保留
→ 旧 source_hash 保留
→ freshness_status = STALE

中文真正重新生成并通过验证/Apply
→ source_hash = 当前西语 hash
→ freshness_status = CURRENT
```

## 17. 日常增量流程

正式 Observation 完成后：

```text
NEW / source_hash changed / NEEDS_REVIEW
↓
Localization Enrichment
↓
Known Resolver
↓
AUTO_READY
或
UNKNOWN
↓
AI（若启用）
↓
Validator
↓
Candidate / Review
↓
必要时 Apply Gate
```

未变化 SKU 不重新 AI 翻译，不改 `updated_at`，不重置 provenance。

## 18. CLI 建议

统一提供：

```text
localization-enrich --run-id <id>
localization-audit --run-id <id>
localization-audit --current
localization-learning-report
localization-promote --candidate-id <id>
localization-apply --run-id <id> --dry-run
localization-apply --run-id <id> --commit
```

兼容：

```text
dictionary-enrich
```

可继续存在，但内部委托 Localization Engine。

## 19. Export / Workspace

Export 和 Workspace 只读取标准解析结果和状态，不重复计算：

```text
value
source
status
freshness
policy_version
```

正式中文表不得默默使用普通西语 fallback。

未知中文：

```text
REVIEW_REQUIRED
```

但不得删除 CURRENT SKU。

## 20. V1 成功标准

V1 完成后应具备：

```text
一套中文标准
一套字段 Planner
一套 Resolver
一套 Validator
一套 AI Adapter
一套 Learning Candidate Pool
一套 Promotion Gate
一套 versioned Localization Apply
一套 Review Queue
一套 Export read model
```

并做到：

> 同一份西语事实在同一 Policy/Knowledge 版本下，重复运行得到确定性一致的字段规划和格式；已有知识不重复调用 AI；未知知识可审计地进入学习闭环。

## V1 Final Closure Addendum

本地 Qwen3:8B 通过可配置的 `LocalOpenAICompatibleProvider` 接入，默认关闭；AI 只处理 UNKNOWN，输出必须经 Validator 后进入统一 Learning Pool。人工批准由 `KnowledgePromotionRouter` 路由到对应知识 CSV，原子写入并更新 manifest，绝不直接修改 PRIMARY 或执行 Git。

### Final Closure Evidence Contract（2026-09-01）

Learning Candidate 的语义身份仍由 `semantic_type + source_term + zh_value` 决定，但事实证据必须按 SKU 保存为结构化 `evidence`：每条包含 `sku`、`source_hash`、`source_run_id`、`source_commit_id` 和 `source_example`。同一 SKU+hash 只保留一条；同一 SKU 出现不同 hash 标记 `EVIDENCE_CONFLICT`，不得静默覆盖。CSV 的 `evidence_json` 是正式 freshness 依据，`evidence_skus` 仅为派生展示列。Promotion 必须逐条核验当前 PRIMARY 的 Localization source hash，任何缺失、非当前 SKU 或不匹配都阻断 promotion。

### Local Qwen training contract（2026-09-01）

本地 Qwen3:8B 的训练数据不是普通的西语→中文平行语料。`scripts/build_local_qwen_dataset.py` 只选可信知识状态，并为每条样本写入完整的字段级规划投影：品名只表达商品身份，规格承载消费者选择参数，分类使用固定受控值，品牌/IP/技术 Token 只在有证据时保留，数字与源事实不可臆造或丢失。样本同时记录 `NAMING_AND_SPEC_PLANNING_STANDARD.md` 和 `CHINESE_LOCALIZATION_STANDARD.md` 的 SHA-256。

训练脚本只接受 schema v2 且同时带有 `naming_policy_version`、字段策略覆盖和源文档 hash 的数据集；旧的简化提示数据会被拒绝。QLoRA 的 loss 只作用于 assistant JSON 输出，不训练模型复读规则和源文本。适配器输出仍是离线候选，必须经过既有 Validator/Learning/Promotion Gate，不能自动写入字典、SQLite PRIMARY 或正式 Export。
# Final field-contract hotfix (2026-09-01)

Localization has one canonical seven-field contract.  Official source facts
flow through the canonical names and then to Chinese storage fields:

| Source | Canonical | Chinese |
|---|---|---|
| name_es | name | name_zh |
| cat1_es | cat1 | cat1_zh |
| cat2_es | cat2 | cat2_zh |
| spec_es | spec | spec_zh |
| unit_price_es | unit_price | unit_price_zh |
| desc_es | description | desc_zh |
| details_es | details | details_zh |

The mapping is defined in `localization/contracts.py`; no module may derive a
field by removing `_zh`.  AI may request only the six non-price canonical
fields.  Source damage blocks the matching canonical field (for example
`desc_es → description`) and never sends either `description` or legacy `desc`
to a provider.

Final validation retains only structural/source reasons and recalculates
field-resolvable reasons after manual overrides.  AI candidates must preserve
source technical tokens such as LED, USB-C, E27 and IP44.  `EVIDENCE_CONFLICT`
is a first-class learning state and blocks promotion until clean evidence is
available.

Manual Override is a terminal SKU × field authority: its value is revalidated
for residual language, numeric facts, technical tokens and source freshness,
but it is not re-requested from AI or replaced by model cache.  Promotion
checks `EVIDENCE_CONFLICT` directly in both the pure decision API and the
router, before freshness checks or dictionary staging.
