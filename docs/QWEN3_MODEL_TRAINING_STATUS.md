# Qwen3 8B 模型训练现状、技术方案与目标

更新时间：2026-09-11  18:35（Asia/Shanghai）
项目：`F:\ActionSKUTracker`  
用途：Action 西班牙官网商品事实的中文标准化，不负责采集、在售判断、价格判断或生命周期判断。

## 1. 当前结论

合并数据整行 QLoRA 和字段条件 QLoRA 两次离线正式实验均已完成。两次实验都从原始 Qwen3-8B 开始，不是在旧 adapter 上叠加，也不是全参数微调；字段条件实验是针对整行模型硬错误开的独立诊断分支。

本轮训练输入已经完成数据合并和泄漏检查：

| 数据来源 | 训练 | 验证 | 测试 |
| --- | ---: | ---: | ---: |
| 2026-09-08 干净金标 | 870 | 107 | 97 |
| 2026-09-10 增量审核通过 | 390 | 49 | 49 |
| 合并数据 | **1,260** | **156** | **146** |

合并校验结果：

- SKU 在训练、验证、测试之间无交叉；
- 六字段 `source_hash` 无跨集合交叉；
- `name/spec/description/details` 自由文本指纹无跨集合交叉；
- 每个集合内部无重复 SKU、无重复源哈希、无重复自由文本指纹；
- 合并 manifest 状态为 `PASS`；
- Master、SQLite、正式字典和生产流程没有被修改。

正式训练已完成，状态记录如下：

- 基础模型：`runtime/models/Qwen3-8B`；
- 训练输出：`runtime/training/qwen3_8b/20260911/combined_gold_incremental/formal_qlora_200_earlystop/`；
- 训练上限：200 个 optimizer steps；
- 验证频率：每 10 步；
- Early stopping patience：3 次验证无改善；
- checkpoint：自动保留验证集最佳 checkpoint；
- 实际完成：190 个 optimizer steps；在连续验证无改善后按 patience=3 早停；
- 最佳 checkpoint：`checkpoint-160`；最终 adapter 已保存到 `adapter/`；
- 最佳验证 loss：约 `0.3116`；最终训练 loss：约 `0.3447`（均仅作训练诊断，不作为上线依据）；
- 训练耗时约 4,204 秒，最高显存约 7.415 GB；
- 没有 OOM、数据读取失败或标签构建错误；当前完整回归测试：`407 passed`（包含合并校验、早停保护、事实 Guard、四方 benchmark 入口、字段条件评估入口、快照完整性保护、146 条复核队列、Hard Test 候选筛选、Error Taxonomy、逐条预测复核、CSV 导出、Hard-Example 审核队列保护、历史训练谱系排除和当前留出集排除测试）。

两次训练结果都不能直接称为“模型通过”：整行 adapter 在 146 条测试集上有 4 个硬错误，字段条件 adapter 在 874 条字段测试集上有 3 个硬错误。因此当前仍不能进入 Stage 5 或接入每日生产流程。

字段条件 SFT 实验已经完成并单独冻结为 `QWEN3_ACTION_FIELD_CONDITIONED_20260911_V1`。它不覆盖、不替换 `QWEN3_ACTION_20260911_V1`，只使用字段级训练/验证集训练，并在结束后使用独立测试集评估；两个快照都不写入 Master、SQLite 或正式字典。

## 1.1 专业评审后的定性

当前问题已经不是“Qwen 能不能学会翻译”，而是“能不能在不改变官网事实的前提下稳定标准化”。

以下问题属于 P0 事实错误，优先级高于中文是否漂亮：

- 数字遗漏，例如 `7` 变成只剩一个 `7`；
- 凭空增加数字，例如源文没有年龄却生成“3 岁以上”；
- 数字、单位、数量或否定词改变；
- `description` 和 `details` 之间发生事实迁移；
- 品牌、型号、系列被改写；
- 一级类目生成出固定闭集之外的值。

前两轮增量训练已经证明：loss 下降不能推出这些错误自然消失。因此本轮将 loss 作为训练诊断，不作为上线依据；上线依据是事实安全门槛、错误分类和源字段复核。

## 2. 业务目标与模型边界

### 2.1 目标

让模型把 Action 西班牙官网的事实字段转换成统一、简洁、可审计的中文：

```text
name_es        -> name_zh
cat1_es        -> cat1_zh
cat2_es        -> cat2_zh
spec_es        -> spec_zh
description_es -> description_zh
details_es     -> details_zh
```

### 2.2 模型不负责的事情

以下字段和判断不训练给模型：

- SKU 身份；
- 当前售价、原价、单价；
- 商品链接、图片链接；
- 今天是否在售；
- NEW、REAPPEARED、MISSING、OFFLINE 等生命周期；
- Sitemap、Listing、Detail 证据的权威性判断；
- Cloudflare、访问中断和 QA 提交决定。

这些仍由官网事实、Presence、Lifecycle、QA、Master/SQLite 主链负责。模型只生成中文派生候选。

## 3. 基础模型情况

基础模型本地路径：`F:\ActionSKUTracker\runtime\models\Qwen3-8B`。

从 `config.json` 实际读取的主要配置：

| 项目 | 值 |
| --- | --- |
| 架构 | `Qwen3ForCausalLM` |
| 模型类型 | `qwen3` |
| 隐藏层数 | 36 |
| 隐藏维度 | 4096 |
| Attention heads | 32 |
| KV heads | 8 |
| 中间层维度 | 12288 |
| 最大位置长度 | 40960 |
| 词表大小 | 151,936 |
| 配置 dtype | bfloat16 |
| Transformers 配置版本 | 4.51.0 |

当前训练机：

- GPU：NVIDIA GeForce RTX 3060；
- 显存：12 GB；
- 合并数据 smoke 峰值：约 7.184 GB；
- smoke 训练：3 步，64 条训练样本、16 条验证样本；
- smoke 结果：训练 loss 约 1.079，验证 loss 约 0.834；
- 结论：显存和训练标签流程可运行。

## 4. QLoRA / LoRA 到底是什么

### 4.1 当前使用的是 QLoRA 训练

当前方案是“4-bit 量化基础模型 + LoRA 低秩适配器”：

```text
Qwen3-8B 基础权重
        ↓ 4-bit NF4 量化（冻结）
量化后的基础模型
        + LoRA 可训练增量参数
        ↓
独立 adapter 目录
```

基础模型权重不被改写，训练只保存 adapter。推理时需要同时加载基础模型和 adapter。

### 4.2 当前 LoRA 配置

训练脚本：`scripts/train_qwen_qlora.py`。

| 配置 | 当前值 | 说明 |
| --- | --- | --- |
| LoRA rank `r` | 16 | 低秩矩阵容量 |
| `lora_alpha` | 32 | LoRA 缩放因子 |
| `lora_dropout` | 0.05 | 防止过拟合 |
| bias | `none` | 不训练 bias |
| task type | `CAUSAL_LM` | 因果语言模型 |
| target modules | `q_proj,k_proj,v_proj,o_proj,gate_proj,up_proj,down_proj` | Attention 和 MLP 投影层 |
| 基础量化 | 4-bit NF4 | 降低显存占用 |
| double quantization | 开启 | 进一步压缩量化参数 |
| 4-bit compute dtype | float16 | RTX 3060 上运行 |
| optimizer | `paged_adamw_8bit` | 低显存优化器 |
| gradient checkpointing | 开启 | 以计算换显存 |
| per-device batch | 1 | 单卡显存约束 |
| gradient accumulation | 8 | 有效 batch 约为 8 |
| max length | 768 | 超长输入触发截断保护 |
| 学习率 | `2e-4` | 当前实验配置 |
| seed/data seed | 42 | 保证可复现 |

### 4.3 Loss 只训练中文答案

训练使用 assistant-only label：

- system 和 user 的输入 token 标签全部设为 `-100`；
- loss 只计算 assistant 中文输出；
- 如果答案被完全截断，直接报 `TARGET_TRUNCATED`，不允许悄悄训练坏数据；
- 这避免模型把 SKU、价格或西班牙语输入本身当作要背诵的答案。

### 4.4 当前不是全参数微调

当前没有训练 Qwen3-8B 的全部参数，因此：

- 不会生成一个独立的完整 8B 权重副本；
- adapter 体积远小于基础模型；
- 需要基础模型与 adapter 配套使用；
- 不能把 adapter 单独当作完整模型交付；
- 若以后要合并 adapter 到基础模型，必须另做离线导出和回归，当前不做。

## 5. 已有模型与训练结果

### 5.1 旧的清洁金标 adapter

路径：`runtime/training/qwen3_8b/20260908/baseline_gold_qlora_8b_20260909_clean/adapter/`

- 训练数据：870 条；
- 验证数据：107 条；
- 最大训练步数：200；
- 训练脚本记录了 assistant-only label、模型配置哈希和数据哈希；
- 作用：当前旧金标基线，仅用于比较；
- 不能直接视为已满足新增 SKU 泛化要求。

### 5.2 新增 488 条的 200 步 adapter

路径：`runtime/training/qwen3_8b/20260910/incremental_488/full_qlora/adapter/`

- 训练数据：390 条；
- 验证数据：49 条；
- 训练步数：200；
- 训练 loss：约 0.212；
- 最佳验证 loss 约在第 50 步，继续训练后出现过拟合迹象；
- Stage 4 发现 2 个硬错误：
  - `2562727`：描述凭空增加数字 `1`；
  - `3010209`：描述凭空增加“3 岁以上”。
- 结论：**不能通过硬门槛**，冻结为失败对照，不再继续在其上训练。

### 5.3 新增 488 条的 60 步 adapter

路径：`runtime/training/qwen3_8b/20260910/incremental_488/short_60_qlora/adapter/`

- 训练数据：390 条；
- 验证数据：49 条；
- 训练步数：60；
- 训练 loss：约 0.434；
- 最佳验证 loss 在第 60 步，约 0.350；
- 硬错误减少，但仍有 `3221933` 描述遗漏一个数字 `7`；
- 严格字段一致率低于字典 Resolver；
- 结论：**仍不能通过 Stage 4**，冻结为失败对照。

### 5.4 当前合并数据正式训练

路径：`runtime/training/qwen3_8b/20260911/combined_gold_incremental/formal_qlora_200_earlystop/`。

- 基础模型：原始 Qwen3-8B；
- 训练数据：1,260 条；
- 验证数据：156 条；
- 测试数据：146 条，训练过程中不读取；
- 最大步数：200；
- 每 10 步验证和保存；
- patience：3；
- `load_best_model_at_end=True`；
- 不在旧 adapter 上叠加；
- 当前状态：已完成，已产生 `checkpoint-140` 至 `checkpoint-190` 及最终 `adapter/`、`training_manifest.json`、`smoke_metrics.json`。
- 训练程序按 patience=3 触发早停，并加载最佳 checkpoint-160 的权重后保存最终 adapter。

## 6. 训练数据与合并产物

合并脚本：`scripts/merge_qwen_gold_datasets.py`。  
合并 manifest：`runtime/training/qwen3_8b/20260911/combined_gold_incremental/qwen_combined_gold_incremental_manifest.json`。

输出文件：

- `qwen_combined_gold_incremental_train.jsonl`：1,260 条；
- `qwen_combined_gold_incremental_validation.jsonl`：156 条；
- `qwen_combined_gold_incremental_test.jsonl`：146 条；
- `qwen_combined_gold_incremental_manifest.json`：输入、输出、行数和 SHA-256。

合并策略不是重新随机拆分，而是保留两批数据原有的 train/validation/test 归属。这样做的原因是：

1. 旧金标测试集已经是冻结的历史对照；
2. 新增 488 条中的 49 条测试集也是独立留出样本；
3. 重新分组可能让人误以为测试集曾参与模型选择；
4. 保留原分组便于审计和重复实验。

## 7. 评估规则（已校正）

评估脚本：`scripts/compare_qwen_baselines.py`。  
当前整行评估 policy version：`stage4_safety_first_v3_2026-09-11`；字段条件评估 policy version：`field_conditioned_safety_v2_2026-09-11`。

### 7.1 自动硬门槛

以下任何一项失败，模型都不能进入 Stage 5：

- JSON 可解析率 100%；
- 字段 schema 正确率 100%；
- 目标中原本非空的字段，输出不能为空；
- 每个字段的数字、百分比、尺寸、数量和单位不能遗漏；
- 不允许凭空增加数字或事实标记；
- 普通西班牙语残留为 0；
- 普通英语残留为 0；
- 一级类目必须属于固定 15 类；
- 不允许把其他字段的数量搬进当前字段。

### 7.2 诊断指标，不再单独决定通过

以下指标只用于比较和定位，不证明语义忠实：

- 严格逐字参考译文一致率；
- 只去除空格、全角符号等格式差异后的归一化一致率；
- 与字典 Resolver 的一致率差值。

原因是：两个中文译法可能都忠实，但字面不同；反过来，字面相同也不能证明没有字段错位。

### 7.3 必须人工完成的语义门槛

自动 Guard 只能发现结构、数字和明显残留，不能完整判断：

- 西语句子的真实语义是否被正确理解；
- 否定词是否被保留；
- 品牌、系列和商品通用名边界是否正确；
- 单位含义是否被误换；
- 描述和详情是否发生事实迁移。

因此自动硬门槛通过后，仍要对分层留出样本及全部 Guard 拒绝项做源字段复核。任何 P0 事实错误都会阻断 Stage 5。

## 7.4 四方 benchmark 与全量 146 条复核

当前合并训练已用同一份冻结的 146 条测试集完成四方对比：

1. Raw Qwen3-8B；
2. 旧的 870 条金标 adapter；
3. 当前 1,260 条合并数据 adapter；
4. Dictionary Resolver。

六个字段必须分别统计：`name`、`cat1`、`cat2`、`spec`、`description`、`details`。不能只报一个总准确率。

由于测试集只有 146 个 SKU，建议对 146 条全部进行人工源字段事实核验，而不是只抽样。即使自动测试为 0/146 P0 错误，按 rule of three 粗略估计，真实错误率的 95% 上界仍约为 `3/146 = 2.1%`；因此 0/146 是必要证据，不是生产安全证明。

## 7.5 Error Taxonomy

评估报告必须把问题从简单的 PASS/FAIL 扩展为可行动的分类：

| 类型 | 含义 |
| --- | --- |
| `NUMERIC_MISSING` | 数字、数量、尺寸或百分比遗漏 |
| `NUMERIC_ADDED` | 源字段不存在的数字被加入 |
| `UNIT_CHANGED` | 单位改变或单位含义丢失 |
| `NEGATION_LOST` | 是/否、不/无等否定关系改变 |
| `BRAND_MODEL_CHANGED` | 品牌、型号、系列被改写或误翻译 |
| `CROSS_FIELD_FACT_MOVE` | 事实从一个字段迁移到另一个字段 |
| `CATEGORY_INVALID` | 一级类目不在固定 15 类中，或二级映射不受控 |
| `SPANISH_RESIDUAL` | 普通西班牙语残留 |
| `SEMANTIC_MISTRANSLATION` | 数字无误但整体语义错误 |
| `STYLE_ONLY_DIFF` | 仅标点、空格或表达风格不同，不算事实错误 |

当前自动 Guard 能稳定发现结构、数字和明显语言残留；`UNIT_CHANGED`、`NEGATION_LOST`、`BRAND_MODEL_CHANGED`、`CROSS_FIELD_FACT_MOVE` 和 `SEMANTIC_MISTRANSLATION` 仍需要字段级人工核验或后续专用规则。

## 7.6 本轮 146 条四方 benchmark 实测结果

报告：[four_way_benchmark_146_policy_v3.json](../runtime/training/qwen3_8b/20260911/combined_gold_incremental/four_way_benchmark_146_policy_v3.json)。四个系统使用同一份冻结测试集、同一提示词和同一安全 Guard；结果只用于离线决策。旧的 `four_way_benchmark_146.json` 保留作历史对照。

| 系统 | JSON/schema | 必填完整率 | 数字保留率 | 数字幻觉率 | 普通西语残留率 | 一级类目合法率 | 硬错误数 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Raw Qwen3-8B | 97.26% / 97.26% | 100% | 99.77% | 0% | 2.46% | 5.63% | 161 |
| 旧 870 条 adapter | 100% / 100% | 100% | 99.77% | 0.11% | 0.11% | 100% | 4 |
| 当前 1,260 条 adapter | 100% / 100% | 100% | 99.89% | 0% | 0.11% | 99.32% | **3** |
| Dictionary Resolver | 100% / 100% | 100% | 99.54% | 0% | 33.33% | 100% | 296 |

当前 adapter 的 3 个自动 Guard 阻断项为：

1. `3211585 / name / NUMERIC_DROPPED`：源名称中的型号 `SL-300` 被输出成“Solix牌无线耳机”；
2. `2553956 / name / SPANISH_RESIDUAL`：`La Sonata Antracita` 中普通颜色词仍未翻译；
3. `2523150 / cat1 / INVALID_CAT1`：源类目为玩具，输出成了“手工制作”，不在正确一级类目。

因此本轮结论是：合并训练相对旧 adapter 有改善，但 `hard_error_count=3`，自动 Stage 4 仍为 FAIL；四方报告明确要求 146 条全部做源字段人工复核，Stage 5 暂不开放。Dictionary Resolver 的残留率较高主要来自其事实优先的西语 fallback，不应把它的逐字残留指标直接等同于模型语义质量。

人工复核队列入口：`scripts/build_qwen_manual_review_queue.py`。本轮队列应包含全部 146 条测试 SKU，默认状态为 `PENDING_MANUAL_REVIEW`；自动 Guard 的 3 条阻断项会附带证据，但没有人工确认前不得改成通过。

## 8. 训练目标

### 8.1 短期目标：完成 Stage 4

当前合并训练的直接目标不是追求最低 loss，而是验证：

1. 合并数据是否比仅用 390 条更稳定；
2. 验证 loss 是否在合理位置停止；
3. 最佳 checkpoint 是否比最后 checkpoint 更可靠；
4. 146 条完全隔离测试集上的硬错误是否为 0；
5. 模型是否至少保持字典 Resolver 的安全水平；
6. 失败时能否按错误类型定位，而不是继续盲调步数。

### 8.2 中期目标：进入离线 Stage 5

只有以下条件全部满足才可进入：

- 合并测试集自动硬门槛通过；
- 与 raw Qwen、旧 adapter、字典 Resolver 完成同口径对比；
- 分层样本源字段复核没有 P0 事实错误；
- 模型输出仍只写候选或 Review Queue；
- 不直接写 Master、SQLite 或正式字典；
- 连续多轮离线验证稳定。

Stage 5 只处理：`NEW`、`source_hash changed`、`NEEDS_REVIEW`。老 SKU 未变化时不产生新翻译。

Stage 5 的核心指标不是全量 SKU 的平均准确率，而是 **Resolver Gap Accuracy**：只统计 Dictionary Resolver 无法可靠解决的字段，观察 Qwen 是否带来可审计的增量价值。每个候选最终归入：

- `ACCEPT_WITHOUT_EDIT`：无需修改即可接受；
- `ACCEPT_WITH_EDIT`：人工修改后接受；
- `REJECT`：拒绝候选；
- `GUARD_REJECT`：被机械安全检查拒绝；
- `P0_FACT_ERROR`：事实错误，必须单独计数。

进入生产讨论前至少连续完成 3 个独立离线批次：

1. 历史 Resolver-gap replay；
2. 真实新增 SKU；
3. `source_hash changed` / `NEEDS_REVIEW` SKU。

每一轮按六字段分别记录上述五类结果，连续多轮 `P0_FACT_ERROR = 0` 后才讨论低风险字段的逐步开放。

字段开放顺序不应相同：`cat1` 继续使用固定 15 类确定性映射；`cat2` 优先使用受控类目字典；`name` 可发挥模型价值但必须严格校验品牌/型号；`spec` 可在数字/单位 Guard 稳定后较早进入 Shadow；`description` 和 `details` 保持更强人工审核，其中 `details` 最后放宽。

### 8.3 长期目标：成为字典的补充，而不是替代

长期生产形态应是：

```text
商品/品牌/类目/术语字典
          ↓ 优先解析
Qwen 候选模型
          ↓ 只处理字典无法可靠覆盖的增量
Guard + Review Queue
          ↓
人工确认后进入字典
          ↓
Export / Master 消费已确认结果
```

模型不应绕过字典，也不应凭模型自信度直接覆盖官网事实。

## 8.4 失败后的分叉策略

如果本轮合并训练仍出现 `NUMERIC_MISSING`、`NUMERIC_ADDED` 或 `CROSS_FIELD_FACT_MOVE`，不再继续盲调 200/100/60 步，也不优先修改 LoRA 参数。下一版本应改为 **Field-conditioned SFT**：输入完整西语上下文并明确 `TARGET_FIELD`，一次只生成一个字段。

例如只训练 `TARGET_FIELD: spec`，输出只能有 `spec_zh`。这样能缩小字段事实边界，让 Validator 逐字段核验，并利用已有字段级样本；但 42,716 条字段样本不能未经审核全部喂入，因为“源可靠”不等于“中文 target 已是金标”。

下一批金标应采用 Error-driven Gold Expansion / Hard Example Mining，优先补充数字密集、年龄、容量、套装数量、否定词、品牌+通用名、系列+型号、多数字描述和长 `details`，而不是随机扩充。

## 8.5 Hard Test 与训练快照

在现有 146 条冻结测试集之外，建立永不参与训练的 Hard Test，第一版建议 200–300 个字段案例，覆盖：`USB-C`、`20D`、`220V`、`A3`、`3×1`、`1.5 L`、`0.5 mm`、套装数量、否定词、防水/不防水、品牌+系列+规格、多数字描述和长详情。

每次模型升级都必须在 Hard Test 上保持 P0 为 0。

本轮已从已批准候选中按数字、单位、否定词和长字段复杂度，确定性筛出 250 条 Hard Test 候选，并按 SKU、六字段西语源指纹、`source_hash` 三重条件排除了本轮 train/validation/test 的全部数据（实测三组指纹交叉均为 0）。当前状态为 `PENDING_MANUAL_REVIEW`，人工核验通过后才能冻结为正式 Hard Test；在此之前不得加入任何训练集或验证集：

- 数据：[qwen_hard_test_v1_candidate.jsonl](../runtime/training/qwen3_8b/20260911/hard_test_v1_candidate/qwen_hard_test_v1_candidate.jsonl)
- Manifest：[qwen_hard_test_v1_candidate.manifest.json](../runtime/training/qwen3_8b/20260911/hard_test_v1_candidate/qwen_hard_test_v1_candidate.manifest.json)

由于本轮 adapter 仍出现数字遗漏/新增，已按失败分叉策略生成 Field-conditioned SFT 候选集：每条样本只暴露一个西语字段，并只要求输出同名中文字段；保留原 train/validation/test SKU 隔离。生成结果为训练 7,553、验证 935、测试 874 个字段样本，拒绝 10 个 `details` 数字不一致样本，manifest 状态为 `READY_FOR_OFFLINE_SMOKE_ONLY`。这不是对当前模型的替换，也尚未启动下一轮训练：

- 数据目录：[field_conditioned_v1](../runtime/training/qwen3_8b/20260911/field_conditioned_v1)
- Manifest：[field_conditioned_manifest.json](../runtime/training/qwen3_8b/20260911/field_conditioned_v1/field_conditioned_manifest.json)
- Field-conditioned smoke 已通过：3 步、64 条训练/16 条验证，训练 loss 约 `1.599`、验证 loss 约 `1.588`，显存峰值约 `6.79GB`；仅证明格式、标签和 CUDA 可运行，不代表模型质量或 Stage 4 通过。随后完成独立的 200 步 early-stopping 正式实验（详见 8.6），结果为 180/200 步早停、最佳 checkpoint-150，并已绑定到独立快照；874 条测试评估仍有 3 个硬错误，因此不能替代生产快照。

本轮正式训练已冻结 `TRAINING_SNAPSHOT_V1`，ID 为 `QWEN3_ACTION_20260911_V1`。快照至少绑定：

- base model 与 tokenizer SHA-256；
- train/validation/test SHA-256；
- 训练脚本版本或 commit；
- evaluation policy 版本；
- seed；
- LoRA/QLoRA 配置；
- best step、best validation loss、final step；
- best checkpoint 与 final checkpoint 路径。

即使该版本最终失败，也必须永久保留，不能覆盖，以便后续 V2/V3 做可重复比较。

### 8.6 已完成的 Field-conditioned 正式实验

由于整行输出在 146 条冻结测试集上仍有 4 个自动硬错误，本轮同时启动了一个隔离的字段条件实验，用来验证“每次只生成一个目标字段”是否能减少数字遗漏、数字幻觉和跨字段事实迁移。它不是盲目继续增加整行训练步数，也不是把旧 adapter 叠加到新 adapter 上。

| 项目 | 当前设置 |
| --- | --- |
| 数据目录 | `runtime/training/qwen3_8b/20260911/field_conditioned_v1/` |
| 训练/验证/测试 | 7,553 / 935 / 874 个字段样本 |
| 样本形式 | 完整西语上下文 + `TARGET_FIELD`，只生成对应中文字段 |
| 训练入口 | `scripts/train_qwen_qlora.py` |
| 输出目录 | `field_conditioned_v1/formal_qlora_200_earlystop/` |
| 最大步数 | 200 |
| Early stopping | patience=3，每 10 步验证，保存最佳 checkpoint |
| max length | 512 |
| seed | 42 |
| 训练方式 | 与正式整行实验相同的 4-bit QLoRA/LoRA 配置 |
| 生产写入 | 禁止；只允许离线评估 |

字段条件正式实验现已完成：180/200 步时因 patience=3 早停，最佳 checkpoint 为 `checkpoint-150`，训练 loss 约 `0.4330`，显存峰值约 `6.892GB`。第 2 步曾出现一次 `grad_norm=nan`，随后 loss 和梯度恢复为有限值，未观察到连续 NaN、OOM 或数据读取失败；这次孤立现象已保留在运行日志中。训练结束后已对独立的 874 条字段测试集完成离线评估，结果见下方 8.7；3 个硬错误已进入只读 Hard-Example 审核队列。

该实验的通过条件仍是事实安全而不是 loss：数字/单位/数量/否定词保持、没有跨字段事实迁移、类目值合法、普通西语残留可控，并完成字段级人工复核。即使字段条件实验指标改善，也不能自动替换当前快照或直接用于生产。

### 8.7 Field-conditioned 874 条测试集结果

报告：[field_conditioned_benchmark_policy_v2.json](../runtime/training/qwen3_8b/20260911/field_conditioned_v1/field_conditioned_benchmark_policy_v2.json)。三方使用完全相同的 874 条字段测试集和字段级 Guard；评估只读，不修改 Master、SQLite 或正式字典。旧的 `field_conditioned_benchmark.json` 仅保留作历史对照。

| 系统 | JSON/schema | 数字保留率 | 数字幻觉率 | 普通西语残留率 | 一级类目合法率 | 硬错误数 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Raw Qwen（字段提示） | 98.28% / 98.28% | 100.00% | 0.00% | 16.41% | 0% | 302 |
| Field-conditioned QLoRA | **100% / 100%** | 99.66% | 0.11% | **0%** | **100%** | **3** |
| Dictionary Resolver（字段输入） | 100% / 100% | 100.00% | 0.00% | 56.52% | 100% | 494 |

Field-conditioned adapter 比 Raw Qwen 大幅减少结构错误、西语残留和非法一级类目，但仍未通过事实安全门槛。以下为按最新 Guard 规则重跑的 874 条结果：

1. `2529028 / spec`：源 `6x25,5x34,5 cm`，输出把小数 `25,5` 拆成 `25、5`，触发 `NUMERIC_DROPPED + NUMERIC_HALLUCINATED`；
2. `3225515 / description`：源文多次出现 `2 en 1`，输出使用“**双面**”替代并丢失同字段中的 `2/1` 数字，触发 `NUMERIC_DROPPED`；
3. `3221933 / description`：源文两次出现数量 `7`，输出只保留一次，触发 `NUMERIC_DROPPED`。

因此本次 Field-conditioned Stage 4 结论仍为 **FAIL / 不可生产**。数字 Guard 已完成规则收窄并在本轮重跑中生效；下一步只能在人工复核这 3 条 Hard-Example、补充非测试集同类金标后再设计新实验。在人工复核 874 条字段测试集和冻结 200–300 条 Hard Test 之前，不得替换 `QWEN3_ACTION_20260911_V1`，也不得启用自动写 Master。

### 8.8 Hard-Example 审核队列（只读）

本轮依据最新 `field_conditioned_qlora` 的 874 条独立测试集报告，生成了 3 条逐字段 Hard-Example 队列。队列绑定了源西语、冻结金标、模型输出、Guard 原因、缺失/新增数字和 `source_hash` 证据；它不是训练集、验证集或 Master 入库文件。

| 项目 | 当前值 |
| --- | --- |
| 队列 | [hard_example_review_v1.jsonl](../runtime/training/qwen3_8b/20260911/field_conditioned_v1/hard_example_review_v1.jsonl) |
| Manifest | [hard_example_review_v1.manifest.json](../runtime/training/qwen3_8b/20260911/field_conditioned_v1/hard_example_review_v1.manifest.json) |
| 待人工复核 | 3 条（`2529028/spec`、`3225515/description`、`3221933/description`） |
| 分类统计 | `NUMERIC_MISSING=3`、`NUMERIC_ADDED=1` |
| 自动训练晋升 | 禁止，必须人工确认后经金标/人工覆盖流水线处理 |
| Master 写入 | 禁止 |

其中 `2529028/spec` 的逗号小数拆分、`3225515/description` 的 `2 en 1` 数字遗漏和 `3221933/description` 的数量 `7` 遗漏均属于明确的数字保真风险；在人工复核前仍按阻断项处理。

### 8.9 无泄漏增量候选审计（2026-09-11）

最初的 500 条候选完成双轮审校后，额外的训练谱系核验发现其 SKU 已出现在既有训练语料中。即使中文字段本身通过审校，也不能把重复训练样本称为“新一轮增量数据”。该 500 条批次仅保留为审计证据，**永久禁止**进入下一次拆分、训练、验证、测试或金标累计。

收集器已改为同时排除：所有历史 `train`、`validation`、`test` 文件，以及所有既往 `qwen_incremental_approved_*` 批次。按此严格规则，当期只有 22 条完整、可审的全字段新 SKU；无法安全凑满 500 条。

| 项目 | 结果 |
| --- | --- |
| 无泄漏候选 | [qwen_incremental_fresh_v2_candidate_22.jsonl](../runtime/training/qwen3_8b/20260911/qwen_incremental_fresh_v2_candidate_22.jsonl) |
| 双轮审计 manifest | [qwen_incremental_fresh_v2_review_22.manifest.json](../runtime/training/qwen3_8b/20260911/qwen_incremental_fresh_v2_review_22.manifest.json) |
| 首轮结果 | PASS 10；REVISE 12 |
| 独立复核 | 12/12 REVISE 已确认；终检后 20 条通过、2 条阻断 |
| 机械终检 | 20 条通过项的源哈希、字段数字/单位、普通语言残留、品牌规则、历史训练/评估/已审批次交叉均为 0 |
| 标签 | 全部 `MODEL_REVIEWED_SILVER`；不能冒充人工金标 |
| 训练资格 | 仅 20 条可作为将来新拆分的候选累计；不足以单独开启训练 |

下一步不是重复训练现有数据，而是只从后续 `NEW` 或 `source_hash changed` SKU 持续收集新源字段；攒够经过独立审校的无泄漏样本后，再单独建立 train/validation/test 拆分。所有候选继续禁止写入 Master、SQLite 或正式字典。

## 9. 为什么此前“反复训练”没有解决问题

从已有结果看，瓶颈不是单纯的训练步数：

- 390 条增量数据用于 200 步时，验证 loss 后段恶化并出现事实幻觉；
- 60 步降低了幻觉，但仍发生数字遗漏；
- 严格逐字一致率不是可靠的语义正确性证明；
- 新增数据没有覆盖所有旧品类和表达方式；
- 字典 Resolver 的西语 fallback 会降低“纯中文”指标，但可能保留事实，不应简单用逐字一致率压过它。

所以本轮改为一次合并训练、早停、最佳 checkpoint、机械安全硬门槛和人工语义复核的组合方案。

## 10. 训练结束后的固定流程

1. 等正式训练自然完成或早停；
2. 检查 `training_manifest.json`、`smoke_metrics.json`、最佳 checkpoint 和实际完成步数；
3. 用 146 条合并测试集运行 raw Qwen、当前 adapter、旧 adapter、字典 Resolver 对比；
4. 输出硬错误明细、数字差异、残留语言、类目非法项和格式诊断；
5. 对 146 条测试集全部进行人工源字段审查，并完成 Error Taxonomy；
6. 冻结 `TRAINING_SNAPSHOT_V1`，保留 best/final checkpoint 和所有 hash；
7. 只有全部门槛通过，才建立 Stage 5 离线候选试运行；
8. Stage 5 连续跑 3 个独立批次，重点统计 Resolver Gap Accuracy；
9. 无论通过或失败，都不得自动写 Master。

## 11. 相关文件

- 训练脚本：[scripts/train_qwen_qlora.py](../scripts/train_qwen_qlora.py)
- 合并脚本：[scripts/merge_qwen_gold_datasets.py](../scripts/merge_qwen_gold_datasets.py)
- 评估脚本：[scripts/compare_qwen_baselines.py](../scripts/compare_qwen_baselines.py)
- 四方评估入口：[scripts/benchmark_qwen_four_way.py](../scripts/benchmark_qwen_four_way.py)
- 训练快照冻结入口：[scripts/freeze_qwen_training_snapshot.py](../scripts/freeze_qwen_training_snapshot.py)
- 训练总计划：[docs/QWEN3_TRAINING_PLAN.md](QWEN3_TRAINING_PLAN.md)
- 合并 manifest：[runtime/training/qwen3_8b/20260911/combined_gold_incremental/qwen_combined_gold_incremental_manifest.json](../runtime/training/qwen3_8b/20260911/combined_gold_incremental/qwen_combined_gold_incremental_manifest.json)
- 训练快照：[runtime/training/qwen3_8b/20260911/combined_gold_incremental/snapshots/QWEN3_ACTION_20260911_V1/snapshot_manifest.json](../runtime/training/qwen3_8b/20260911/combined_gold_incremental/snapshots/QWEN3_ACTION_20260911_V1/snapshot_manifest.json)
- 四方 benchmark：[four_way_benchmark_146_policy_v3.json](../runtime/training/qwen3_8b/20260911/combined_gold_incremental/four_way_benchmark_146_policy_v3.json)
- 人工复核队列生成器：[scripts/build_qwen_manual_review_queue.py](../scripts/build_qwen_manual_review_queue.py)
- 本轮人工复核队列：[runtime/training/qwen3_8b/20260911/combined_gold_incremental/manual_source_fidelity_review_146.jsonl](../runtime/training/qwen3_8b/20260911/combined_gold_incremental/manual_source_fidelity_review_146.jsonl)
- 逐条模型输出复核生成器：[scripts/dump_qwen_predictions_for_review.py](../scripts/dump_qwen_predictions_for_review.py)
- 含当前 adapter 逐条输出的复核文件：[runtime/training/qwen3_8b/20260911/combined_gold_incremental/manual_source_fidelity_review_146_with_predictions.jsonl](../runtime/training/qwen3_8b/20260911/combined_gold_incremental/manual_source_fidelity_review_146_with_predictions.jsonl)
- Excel 可打开的复核 CSV：[runtime/training/qwen3_8b/20260911/combined_gold_incremental/manual_source_fidelity_review_146.csv](../runtime/training/qwen3_8b/20260911/combined_gold_incremental/manual_source_fidelity_review_146.csv)
- CSV 导出器：[scripts/export_qwen_review_csv.py](../scripts/export_qwen_review_csv.py)
- Hard Test 候选生成器：[scripts/build_qwen_hard_test.py](../scripts/build_qwen_hard_test.py)
- Hard-Example 审核队列生成器：[scripts/build_qwen_hard_example_expansion.py](../scripts/build_qwen_hard_example_expansion.py)
- Field-conditioned 数据生成器：[scripts/build_field_conditioned_gold_dataset.py](../scripts/build_field_conditioned_gold_dataset.py)
- Error Taxonomy 生成器：[scripts/build_qwen_error_taxonomy.py](../scripts/build_qwen_error_taxonomy.py)
- 本轮 Error Taxonomy：[runtime/training/qwen3_8b/20260911/combined_gold_incremental/error_taxonomy_current_adapter.json](../runtime/training/qwen3_8b/20260911/combined_gold_incremental/error_taxonomy_current_adapter.json)
- 字段条件评估器：[scripts/compare_qwen_field_conditioned.py](../scripts/compare_qwen_field_conditioned.py)
- 字段条件训练快照：[snapshot_manifest.json](../runtime/training/qwen3_8b/20260911/field_conditioned_v1/snapshots/QWEN3_ACTION_FIELD_CONDITIONED_20260911_V1/snapshot_manifest.json)
- 字段条件 874 条评估报告：[field_conditioned_benchmark_policy_v2.json](../runtime/training/qwen3_8b/20260911/field_conditioned_v1/field_conditioned_benchmark_policy_v2.json)
- 字段条件 Hard-Example 队列：[hard_example_review_v1.jsonl](../runtime/training/qwen3_8b/20260911/field_conditioned_v1/hard_example_review_v1.jsonl)

## 12. 当前不可做的事情

- 不把当前运行中的 adapter 当成可用模型；
- 不在旧 adapter 上继续叠加训练；
- 不用训练集指标替代测试集；
- 不因逐字一致率低就放宽数字/单位安全规则；
- 不用模型自动修改 Master、SQLite、正式字典；
- 不把 loss 下降直接解释成翻译质量通过；
- 不把任何一次训练结果写成永久业务规则。
