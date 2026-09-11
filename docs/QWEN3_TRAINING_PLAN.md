# Qwen3 8B Action 中文标准化训练计划

## 目标

让 Qwen3 8B 学会把 Action 西班牙官网事实转换为统一、简洁、可审计的中文字段。模型只负责语言标准化，不负责判断 SKU 是否在售、价格、生命周期或官网事实。

## 字段边界

输入字段：`name_es、cat1_es、cat2_es、spec_es、description_es、details_es`。

输出字段：`name_zh、cat1_zh、cat2_zh、spec_zh、description_zh、details_zh`。

SKU、价格、链接、在售状态、事件和 source_hash 只保存在元数据中，不作为模型学习目标。

## 阶段与门槛

### 阶段 0：源数据筛选

- 从 9,033 条长期商品中筛选源字段完整记录。
- 排除 `SOURCE_DAMAGED`、`SOURCE_POLLUTED`、空西语字段、重复 SKU。
- 当前已得到 8,907 条源可靠 SKU；其中 126 条源损坏/污染已隔离。
- 其中品名/规格完整记录为 8,853 条；该数量来自当前字典与源损坏清单的实际审计。
- 六字段级样本已生成 42,716 条，重复键为 0；其中 775 条字段来自人工确认裁决。

### 阶段 1：字典与候选生成

- 优先使用商品、品牌、类目、术语和人工覆盖字典。
- DeepSeek 只生成候选，不直接写 Master。
- 必查：数字、单位、尺寸、数量、是/否、品牌/型号、15 个一级类目。
- 任何源数据不足、无法判断或凭空增加事实的记录标为 `REJECT`。

### 阶段 2：金标复核

- 从候选中抽取 1,200 条，优先覆盖数字警告、15 个一级类目、新品和复杂详情。
- DeepSeek 输出 `PASS / REVISE / REJECT` 及修正版；模型结果仍需规则检查。
- 金标入选条件：六字段逐项对源、数字和单位检查通过；`REJECT` 不进入训练。
- DeepSeek 首轮复核已完成 1,200/1,200：PASS 848、REVISE 352。
- 第二轮对 352 条修订完成 344 条确认、8 条拒绝；其中 7 条已人工确认修复，3016272 因官网源字段冲突排除。
- 当前有 117 条存在任一字段数字差异，已从干净金标隔离；干净金标为 1,074 条。
- 数字差异记录保存在隔离清单中，不能直接用于训练。

### 阶段 3：训练/验证/测试集

- 金标和确认银标按 SKU 哈希拆分，禁止同一 SKU 跨集合。
- 建议比例：训练 80%、验证 10%、测试 10%。
- 训练集不得含占位符、普通西语残留、源污染或未解决数字错误。
- 测试集固定保存，不参与后续微调。
- 干净金标已按 SKU 拆分：训练 870、验证 107、测试 97，集合无重叠。
- 全量字段集已按 SKU 拆分：训练 36,238、验证 3,212、测试 3,266，重复键 0、SKU 集合无重叠。cat1/cat2 属于固定闭集，源/目标组合允许跨集合重复，单独统计为 302 个组合重叠；自由文本字段跨集合指纹重叠为 0。

### 阶段 4：Qwen 微调与评估

- 先用 QLoRA/LoRA，不改生产主链。
- 评估维度：JSON/schema、必填字段完整率、数字/单位保留率、数字幻觉、品牌保留、类目合法率、普通西语/英语残留率和参考译文一致性。
- 必须同时对比：原始 Qwen、微调 Qwen、现有字典 Resolver。
- 自动硬门槛：JSON/schema 100%，目标非空字段完整率 100%，数字/单位不丢失、不新增，普通西语/英语残留为 0，一级类目必须属于固定 15 类。任一项失败即不得进入 Stage 5。
- 严格逐字一致率与格式归一化一致率只用于诊断，不再作为自动放行条件；不同但忠实的中文表达不应被当作失败，也不能据此证明语义正确。
- 自动硬门槛通过后，仍必须对固定的分层留出样本和所有 Guard 拒绝项做西语源字段人工忠实度复核；出现任何 P0 事实错误则不得进入离线试运行。

### 2026-09-11 合并数据与评估校正

此前仅以新增 488 条做 200 步和 60 步增量训练，均未通过硬门槛：200 步出现数字新增/事实臆造，60 步仍出现数字遗漏。因此冻结两个 adapter 仅作对照，不再围绕同一小批样本反复调步数。

下一次正式训练使用 `scripts/merge_qwen_gold_datasets.py` 合并两套已审核、已固定拆分的数据，并保留每条记录原有的 train/validation/test 归属：

| 来源 | 训练 | 验证 | 测试 |
| --- | ---: | ---: | ---: |
| 2026-09-08 干净金标 | 870 | 107 | 97 |
| 2026-09-10 增量审核通过 | 390 | 49 | 49 |
| 合并后 | 1,260 | 156 | 146 |

合并器逐行重算六字段 `source_hash`，拒绝空/重复 SKU，并对 train/validation/test 做 SKU、source hash 与自由文本指纹的跨集合泄漏检查。输出位于 `runtime/training/qwen3_8b/20260911/combined_gold_incremental/`，由 manifest 绑定全部输入/输出 SHA-256。

正式训练必须从原始 `runtime/models/Qwen3-8B` 开始，不能在旧 adapter 上叠加训练；保留验证集早停和最佳 checkpoint。合并测试集的 146 条记录在训练与 checkpoint 选择期间完全隔离。评估器 `scripts/compare_qwen_baselines.py` 的 policy version 为 `stage4_safety_first_v2_2026-09-11`；它只生成只读报告，不写 Master、字典或模型缓存。

### 阶段 5：离线接入

- 只处理 NEW、source_hash changed、NEEDS_REVIEW SKU。
- 模型输出先写候选/审核队列，不直接写 Master。
- 连续多轮离线验证通过后，再申请接入每日流程。

## 当前产物

- 当前模型与 QLoRA/LoRA 状态：[QWEN3_MODEL_TRAINING_STATUS.md](QWEN3_MODEL_TRAINING_STATUS.md)

- 字段级全量候选：`runtime/training/qwen3_8b/20260908/qwen_field_examples_all.jsonl`
- 初始 5,000 条整行候选：`runtime/training/qwen3_8b/20260908/qwen_candidates_5000.jsonl`
- 金标复核结果：`runtime/training/qwen3_8b/20260908/qwen_gold_review_1200.jsonl`
- 干净金标：`runtime/training/qwen3_8b/20260908/qwen_gold_clean.jsonl`
- 关键数字隔离清单：`runtime/training/qwen3_8b/20260908/qwen_gold_critical_numeric_review.jsonl`
- 金标进度：`runtime/training/qwen3_8b/20260908/gold_review_progress.json`
- 最终审查：`runtime/training/qwen3_8b/20260908/final_dataset_audit.json`
- 8 条拒绝记录的人工裁决：`runtime/training/qwen3_8b/20260908/qwen_human_resolutions_8.jsonl`
- 人工裁决后的审核队列：`runtime/training/qwen3_8b/20260908/qwen_full_review_queue_9033_after_human_resolution.csv`

## 明确禁止

- 不把 SKU、价格或链接训练成记忆。
- 不把源污染和无法核验的数据强行翻译。
- 不用未经复核的模型输出直接覆盖字典或 Master。
- 不以训练集通过替代测试集通过。

## 训练前强制门槛

- QLoRA loss 只计算 assistant 中文答案，system/user 输入必须使用 `-100` 掩码；
- 训练必须记录模型配置、数据 SHA-256、标签策略、长度、步数和随机种子 manifest；
- 评估必须单独报告 JSON 结构、非空、数字保留、数字幻觉、普通西语残留和固定一级类目合法率；
- cat1/cat2 按固定闭集评估，不能把重复的类目映射当作自由文本泛化能力；
- 正式全量训练前先完成清洁金标 smoke/baseline，并保留独立测试集。
