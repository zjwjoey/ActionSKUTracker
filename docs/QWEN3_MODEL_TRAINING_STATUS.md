# Qwen3 8B 模型训练现状、技术方案与目标

更新时间：2026-09-11  10:31（Asia/Shanghai）  
项目：`F:\ActionSKUTracker`  
用途：Action 西班牙官网商品事实的中文标准化，不负责采集、在售判断、价格判断或生命周期判断。

## 1. 当前结论

当前正在进行的是一次新的、合并数据后的 QLoRA 训练。它不是在旧模型上继续叠加，也不是全参数微调。

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

正式训练当前已启动，状态记录如下：

- 基础模型：`runtime/models/Qwen3-8B`；
- 训练输出：`runtime/training/qwen3_8b/20260911/combined_gold_incremental/formal_qlora_200_earlystop/`；
- 训练上限：200 个 optimizer steps；
- 验证频率：每 10 步；
- Early stopping patience：3 次验证无改善；
- checkpoint：自动保留验证集最佳 checkpoint；
- 记录时间点：约第 10/200 步，首次完整验证尚未结束；
- 当前没有 OOM、数据读取失败或标签构建错误。

训练完成前，不能把本轮结果称为“模型通过”，也不能进入 Stage 5 或接入每日生产流程。

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
- 当前状态：运行中，尚未产生最终 adapter/manifest。

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
当前 policy version：`stage4_safety_first_v2_2026-09-11`。

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
5. 对分层样本进行人工源字段审查；
6. 只有全部门槛通过，才建立 Stage 5 离线候选试运行；
7. 无论通过或失败，都不得自动写 Master。

## 11. 相关文件

- 训练脚本：[scripts/train_qwen_qlora.py](../scripts/train_qwen_qlora.py)
- 合并脚本：[scripts/merge_qwen_gold_datasets.py](../scripts/merge_qwen_gold_datasets.py)
- 评估脚本：[scripts/compare_qwen_baselines.py](../scripts/compare_qwen_baselines.py)
- 训练总计划：[docs/QWEN3_TRAINING_PLAN.md](QWEN3_TRAINING_PLAN.md)
- 合并 manifest：[runtime/training/qwen3_8b/20260911/combined_gold_incremental/qwen_combined_gold_incremental_manifest.json](../runtime/training/qwen3_8b/20260911/combined_gold_incremental/qwen_combined_gold_incremental_manifest.json)

## 12. 当前不可做的事情

- 不把当前运行中的 adapter 当成可用模型；
- 不在旧 adapter 上继续叠加训练；
- 不用训练集指标替代测试集；
- 不因逐字一致率低就放宽数字/单位安全规则；
- 不用模型自动修改 Master、SQLite、正式字典；
- 不把 loss 下降直接解释成翻译质量通过；
- 不把任何一次训练结果写成永久业务规则。
