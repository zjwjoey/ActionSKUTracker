# ActionSKUTracker — Qwen Stage 4 恢复与收口计划

审计日期：2026-09-11（Asia/Shanghai）  
审计范围：`F:\ActionSKUTracker` 本地仓库、训练数据、适配器、评估产物和训练环境  
审计性质：只读审计 + 单样本离线加载 smoke；未训练、未改生产配置、未写 Master/SQLite/字典

## 证据标记

- **CONFIRMED**：可由本机文件、日志、命令输出、哈希或指标直接复核。
- **INFERRED**：由已确认事实推导出的工程判断，仍需专项检查才能最终定性。
- **UNKNOWN**：当前产物没有足够证据；不得用假设补齐，也不得据此放行。

## 执行摘要

**PLANNING VERDICT：`READY_TO_EXECUTE_STAGE4_RECOVERY = YES`。** 这里的 YES 只表示恢复计划可以开始执行，不表示当前模型通过 Stage 4。

**CURRENT STAGE 4：FAIL。** 当前两个主要候选适配器均可加载，但都仍有 3 条自动硬错误；现有 146 条整行测试集全部是模型复核银标，没有独立人工金标测试集；启发式审计还发现 train/test 间存在 26 个归一化品名商品族交叉，涉及 28 条 test 记录。整行适配器的正式评估又启用了训练时没有使用的 `STRICT_SYSTEM`，因此 `TRAIN_INFER_PARITY = FAIL`。

**RETRAIN REQUIRED：CONDITIONAL。** 先修数据谱系、group split、训练/推理合同和评估快照，再用现有适配器重评。只有完成这些步骤后仍存在经确认的模型错误，才做最小范围 targeted retrain；当前证据不支持 broad retrain。

---

## 1. Current Reality

### 1.1 Repository

| 项目 | 当前值 | 结论 |
| --- | --- | --- |
| repo | `F:\ActionSKUTracker` | **CONFIRMED**，`git rev-parse --show-toplevel` |
| branch | `feat/export-foundation-v1` | **CONFIRMED**，`git status --branch` |
| HEAD | `1dc2fc0338c2b6ace73b4869b11a0ebfd1179c35` | **CONFIRMED**，本地与 `origin/feat/export-foundation-v1` 一致 |
| worktree | dirty | **CONFIRMED**；训练文档、评估器、Guard、测试及未跟踪训练脚本均有本地改动 |
| production AI | 未启用 | **CONFIRMED**，现有训练文档和 snapshot scope 均限定 offline evaluation |

当前 dirty worktree 不等于数据丢失，但意味着现有快照不能被当作“可从干净 commit 完全复现”的发布快照。恢复阶段必须保留现状，不 reset、不覆盖；待证据冻结后另建干净、可追踪的 Stage 4 recovery commit。

### 1.2 Base model and runtimes

| 项目 | 当前值 | 证据与判断 |
| --- | --- | --- |
| HF base model | `F:\ActionSKUTracker\runtime\models\Qwen3-8B` | **CONFIRMED**，目录约 16.40 GB |
| model type | `qwen3` / `Qwen3ForCausalLM` | **CONFIRMED**，`config.json` |
| HF revision | `b968826d9c46dd6066d109eabc6255188de91218` | **CONFIRMED**，本机 HF cache revision |
| config SHA-256 | `f7c4eadfbbf522470667b797a3c89be2524832d2d599797248dc304fff447c30` | **CONFIRMED** |
| tokenizer SHA-256 | `20b2e9e42943ba42526158348b44d22714d2629321fc4feaaa3f1277516d2aa9` | **CONFIRMED**，snapshot 绑定的 tokenizer 文件集合 |
| chat template | 存在，训练时 `enable_thinking=False` | **CONFIRMED**，tokenizer 配置与训练脚本 |
| Ollama model | `qwen3:8b`，约 5.2 GB，ID `500a1f067a9f` | **CONFIRMED**，本机 Ollama inventory |
| Ollama/HF 等价性 | 未证明 | **UNKNOWN**；Ollama 模型不参与当前 QLoRA 训练与正式评估，不得混用 |

### 1.3 Current candidate adapters

当前不存在 production adapter。真正需要保留并重评的候选有两个：

1. 整行六字段候选：`F:\ActionSKUTracker\runtime\training\qwen3_8b\20260911\combined_gold_incremental\formal_qlora_200_earlystop\adapter`
2. 字段条件候选：`F:\ActionSKUTracker\runtime\training\qwen3_8b\20260911\field_conditioned_v1\formal_qlora_200_earlystop\adapter`

两者均为 LoRA `r=16`、`alpha=32`、`dropout=0.05`，绑定同一个 HF base model；均为 **Stage 4 FAIL / offline only**。

### 1.4 Current blocker in one sentence

**CONFIRMED：Stage 4 卡在“可信评估闭环”而不是 CUDA、显存或 loss。** 现有适配器能训练、能加载、能输出合法 JSON，但测试标签独立性不足、group split 未完成、整行评估提示词与训练不一致、评估 policy 与快照版本漂移，并仍有硬事实/技术 token/类目错误。

---

## 2. Existing Stage Definitions

| Stage | 原计划目标 | 实际实现与完成产物 | 实际阻塞 |
| --- | --- | --- | --- |
| Stage 0 | 从长期商品中筛可靠西语源 | **CONFIRMED**：9,033 SKU；8,907 标记为源可靠；126 源损坏/污染隔离；8,853 品名/规格完整 | 源 snapshot 未绑定 SQLite 文件哈希/commit、字典版本和 normalization 版本 |
| Stage 1 | 字典优先，模型只生成候选 | **CONFIRMED**：5,000 整行候选和 42,701+ 字段样本已生成；有数字/残留/类目 Guard | 生成谱系没有统一 manifest；候选与“Gold”命名混用 |
| Stage 2 | 复核 1,200 条金标 | **CONFIRMED**：848 PASS、352 REVISE；最终 `qwen_gold_clean` 1,074 条 | 实际 1,074 条中 1,068 为模型复核银标、仅 6 条 human-confirmed；“clean gold”名称高估了证据等级 |
| Stage 3 | 以 SKU 拆 train/val/test，禁止泄漏 | **CONFIRMED**：合并集 1,260/156/146；SKU、source_hash、自由文本 exact fingerprint 跨集为 0 | **INFERRED**：商品族泄漏存在；时间泄漏未审；测试集全部为银标 |
| Stage 4 | QLoRA + 多基线 + 硬事实门禁 + 人工忠实度复核 | **CONFIRMED**：整行、字段条件各完成一次 early-stop 训练与自动评估 | 两候选均 3 个硬错误；146/874 人工忠实度未闭环；合同/快照 policy 不一致 |
| Stage 5 | 仅对 NEW、source_hash changed、NEEDS_REVIEW 离线接入 | **2026-09-12 已启动离线 shadow bootstrap**：规则/字典先闭合字段，模型只补字段 gap，Guard 后仅生成候选 | 仅允许 shadow；Stage 4 release 债务未清，禁止生产接入或训练准入 |

原 Stage 1–5 的业务边界总体正确，但 Stage 2 的标签等级、Stage 3 的分组策略、Stage 4 的合同冻结与测试面必须重定义。

---

## 3. Current Stage 4 Blocker

### P0 — 不修就无法可信进入 Stage 5

1. **测试集不是独立人工金标。** **CONFIRMED**：当前合并 1,562 条中，31 条可识别为人工确认/人工复核，1,531 条为模型复核银标；146 条整行 test 全部是模型复核银标。字段转换器又把所有 874 条 test 统一写成 `COMBINED_GOLD`，丢失原始 tier。
2. **商品族 group split 未完成。** **INFERRED**：按西语品名小写、去重音、去数字/常见单位后的确定性启发式检查，train/test 有 26 个共同品名族，涉及 57 条 train 和 28 条 test；validation/test 有 4 个共同族。现有 manifest 只证明 SKU/hash/exact text 不重叠，不能证明系列或模板不泄漏。
3. **TRAIN/INFER 合同不一致。** **CONFIRMED**：整行训练集使用两个较短 system prompt；当前四方报告 `strict_prompt=true`，评估时替换成训练中未出现的 `STRICT_SYSTEM`。结果不能作为同合同下的正式准入指标。
4. **硬错误仍非零。** **CONFIRMED**：整行当前候选 3 条；字段条件候选 3 条。现行合同规定 hard factual errors 必须为 0。
5. **独立人工验收和 Hard/Temporal/OOD 测试未冻结。** **CONFIRMED**：146 条人工源忠实度队列仍 `PENDING_MANUAL_REVIEW`；250 条 Hard Test 仍是 candidate；没有可验证的 temporal test 报告。
6. **训练源谱系不完整。** **CONFIRMED**：数据 manifest 没有记录源 SQLite 文件 SHA、源数据库 commit/version、dictionary manifest/version、normalization version。无法回答“该标签由哪个确定版本生成”。

### P1 — 显著影响 Stage 4 质量

1. **Snapshot 与当前 evaluator policy 漂移。** 整行 snapshot 绑定 v2，当前报告为 v3；字段 snapshot 绑定 v1，当前报告为 v2。**CONFIRMED**。
2. **字段数据标签等级被扁平化。** `build_field_conditioned_gold_dataset.py` 无条件写 `label_tier=COMBINED_GOLD`，无法追溯 human/silver。**CONFIRMED**。
3. **1 条字段训练目标发生截断。** SKU `3000034` 的 description 完整序列 589 token，训练 `max_length=512`；其目标还遗漏源中的 `TP-link`。**CONFIRMED**（tokenizer 实测 + 样本内容）。
4. **当前环境没有正式 environment manifest。** 项目专用 venv 可用，但系统 Python 缺少 PEFT/TRL/datasets/bitsandbytes。若入口选错会直接失败。**CONFIRMED**。
5. **字段正式训练缺独立 stdout/stderr 日志。** trainer state 和 manifest 存在，但无法完整复核启动命令与警告流。**CONFIRMED**。
6. **训练与推理 dtype 不完全一致。** QLoRA compute 使用 bfloat16，当前 evaluator 以 float16 加载；尚无证据证明该差异影响结果，但应纳入 parity manifest。**CONFIRMED / 影响 UNKNOWN**。

### P2 — 可在 Stage 5 后继续优化

1. 精确参考译文一致率不足以衡量可接受中文，应改用四档人工质量等级；这是评估完善项，不替代硬事实门禁。
2. 可继续扩大罕见品类和长 details 的 OOD 覆盖，但不需要先追求“完美全量模型”。
3. Ollama 本地模型可保留作交互试验，但不应纳入当前 HF QLoRA 发布链。

---

## 4. Data Audit

### 4.1 Data lineage and tiers

| 数据集 | 行数 | 当前可证明的等级 | 结论 |
| --- | ---: | --- | --- |
| 长期商品池 | 9,033 SKU | 官网/PRIMARY 派生 | 126 条 source blocked；数据源版本未完全冻结 |
| 源可靠 SKU | 8,907 | 结构/规则筛选通过 | 可作为候选源，不自动等于 Gold |
| 初始整行候选 | 5,000 | 字典/现有中文派生 | 不可直接作为正式 Gold |
| 首轮复核 | 1,200 | DeepSeek PASS/REVISE | 模型复核，不等于人审 |
| `qwen_gold_clean` | 1,074 | 1,068 model-reviewed silver + 6 human-confirmed | 目录名需在新 manifest 中纠正语义 |
| 增量 488 | 488 | 463 model-reviewed silver + 25 human-reviewed gold | 已固定拆分，但 test 中 49 条均为 silver |
| 当前合并集 | 1,562 | 1,531 silver + 31 human | 训练候选可用；不能充当独立金标验收集 |
| 无泄漏新候选 v2 | 22 | 20 silver；2 blocked | 20 条只可累计或留作 temporal 候选，不能单独训练 |
| Hard Test candidate | 250 | 未人审 | `PENDING_MANUAL_REVIEW`，不得进 train/val |

以上 tier 统计来自当前 JSONL metadata：**CONFIRMED**。SQLite commit、dictionary version、normalization version：**UNKNOWN**。

### 4.2 Structural and leakage audit

- broken JSON、空 SKU、重复 SKU：当前合并 manifest 为 0，**CONFIRMED**。
- train/validation/test 的 SKU、source_hash、exact free-text fingerprint 交叉：0，**CONFIRMED**。
- near duplicate / product-family leakage：存在明显候选，**INFERRED**。例如 `Caja de almacenaje Heidrun`、`Bóxers Ziki`、`Calcetines de deporte Kappa/Lotto` 等系列跨 split。
- temporal leakage：**UNKNOWN**。metadata 没有可靠 observation date/snapshot date，当前拆分也不是按时间留出。
- encoding：当前 JSONL 均可按 UTF-8 读取；未发现 broken JSON，**CONFIRMED**。
- target truncation：字段训练集 1 条，整行 train/test 未发现超过各自 768 token 上限；字段 test 未超过 512，**CONFIRMED**。

### 4.3 Required classification before any retrain

| 分类 | 定义 | 当前动作 |
| --- | --- | --- |
| clean | 人工确认、源和目标一致、谱系完整、无泄漏/截断 | 可进 train；独立 test 必须全部属于此类 |
| repairable | 标签、字段位置、格式或 metadata 可由可信证据修复 | 修复后重新 hash 和人审，不原地覆盖冻结集 |
| exclude | SOURCE_DAMAGED、SOURCE_POLLUTED、AMBIGUOUS、合同无法判定 | 永久隔离并记录原因 |
| manual-review | 银标、品牌歧义、来源冲突、Hard Test 候选 | 未确认前不得升级 Gold |

恢复执行时必须生成逐行 `dataset_audit.jsonl/csv`，明确 `source_tier`、`label_tier`、`reviewer_type`、`source_snapshot_id`、`dictionary_version`、`normalization_version` 和 disposition。

---

## 5. Environment Audit

### 5.1 Confirmed training environment

| 项目 | 值 | 状态 |
| --- | --- | --- |
| OS | Windows 11，build `10.0.26200` | **CONFIRMED** |
| Python | 3.12.10 | **CONFIRMED** |
| training venv | `F:\ActionSKUTracker\runtime\training\qwen3_8b\.venv` | **CONFIRMED**，正式训练产物所需包齐全 |
| torch | `2.14.0+cu126` | **CONFIRMED** |
| CUDA runtime | 12.6 | **CONFIRMED**，`torch.cuda.is_available() == True` |
| Transformers | 5.16.1 | **CONFIRMED** |
| PEFT / TRL | 0.20.0 / 1.12.0 | **CONFIRMED** |
| bitsandbytes / accelerate | 0.50.2 / 1.14.0 | **CONFIRMED** |
| GPU | NVIDIA GeForce RTX 3060，compute capability 8.6 | **CONFIRMED** |
| VRAM | 12,288 MiB；审计时约 9,950 MiB free | **CONFIRMED**，`nvidia-smi` |
| RAM | 31.87 GiB | **CONFIRMED** |
| F: free | 约 856.1 GB | **CONFIRMED** |
| C: free | 约 59.35 GB | **CONFIRMED** |

系统 Python 位于 `C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe`，当前没有 datasets、PEFT、TRL、bitsandbytes、accelerate。另有 `F:\LocalAI\train-env`，其 torch/transformers 版本又与项目 venv 不同。**P1 结论：所有 Stage 4 命令必须使用项目 venv 的绝对 Python 路径，禁止裸 `python`。**

### 5.2 Runtime risk paths

- CUDA OOM：当前两轮峰值仅 6.892–7.415 GB，12 GB 显存足够现有 QLoRA 配置；若 OOM，先减 batch/max length，禁止自动 CPU fallback。
- disk shortage：F: 当前充足；所有 checkpoint 必须继续放 F:，不得落到 C: cache。
- wrong model path：训练和评估只接受 HF base path及其 config/tokenizer hash；不得指向 `OLLAMA_MODELS`。
- dependency mismatch：执行前比对冻结的 environment manifest；版本不符即停止，不用“重训”掩盖环境问题。

---

## 6. Adapter Audit

### 6.1 Inventory

| adapter_id | absolute path | base | 角色/状态 |
| --- | --- | --- | --- |
| smoke_0.6b | `F:\ActionSKUTracker\runtime\training\qwen3_8b\20260908\smoke_qlora_0.6b\adapter` | Qwen3-0.6B | 环境 smoke，不参与正式比较 |
| smoke_8b | `F:\ActionSKUTracker\runtime\training\qwen3_8b\20260908\smoke_qlora_8b\adapter` | Qwen3-8B | 历史 smoke |
| smoke_8b_masked | `F:\ActionSKUTracker\runtime\training\qwen3_8b\20260908\smoke_qlora_8b_masked\adapter` | Qwen3-8B | assistant-only label smoke |
| smoke_clean_8b | `F:\ActionSKUTracker\runtime\training\qwen3_8b\20260908\smoke_clean_qlora_8b_20260909\adapter` | Qwen3-8B | clean-data smoke |
| baseline_gold | `F:\ActionSKUTracker\runtime\training\qwen3_8b\20260908\baseline_gold_qlora_8b_20260909\adapter` | Qwen3-8B | 历史基线 |
| baseline_gold_clean | `F:\ActionSKUTracker\runtime\training\qwen3_8b\20260908\baseline_gold_qlora_8b_20260909_clean\adapter` | Qwen3-8B | 四方 benchmark old adapter |
| incremental_488_smoke | `F:\ActionSKUTracker\runtime\training\qwen3_8b\20260910\incremental_488\smoke_qlora\adapter` | Qwen3-8B | smoke |
| incremental_488_full | `F:\ActionSKUTracker\runtime\training\qwen3_8b\20260910\incremental_488\full_qlora\adapter` | Qwen3-8B | 200 步失败对照 |
| incremental_488_short | `F:\ActionSKUTracker\runtime\training\qwen3_8b\20260910\incremental_488\short_60_qlora\adapter` | Qwen3-8B | 60 步失败对照 |
| combined_smoke | `F:\ActionSKUTracker\runtime\training\qwen3_8b\20260911\combined_gold_incremental\smoke_qlora\adapter` | Qwen3-8B | 合并数据 smoke |
| combined_current | `F:\ActionSKUTracker\runtime\training\qwen3_8b\20260911\combined_gold_incremental\formal_qlora_200_earlystop\adapter` | Qwen3-8B | 当前整行候选，Stage 4 FAIL |
| field_conditioned_current | `F:\ActionSKUTracker\runtime\training\qwen3_8b\20260911\field_conditioned_v1\formal_qlora_200_earlystop\adapter` | Qwen3-8B | 当前字段候选，Stage 4 FAIL |

所有 8B adapter model 均约 174,655,536 bytes，配置均为 LoRA `r=16/alpha=32`；**CONFIRMED**。关键候选哈希：

- combined adapter model SHA-256：`caff23eec782ea2be8a73b9fbe076db2868a9a70c23386823d10c5528e3fcc66`
- combined adapter config SHA-256：`784216f85f23d33cecf1cedce9d596cba268f5b30c9dfac3faea16b271a8d4a4`
- combined snapshot tree SHA-256：`dabc9294fa553d60eddb3a1a0c8939117b2bfefd8527d2f1742bdd6ccfbb1d0c`
- field adapter model SHA-256：`16fbe69e1d37bc54cb7a7f8fcd3e865762edacc208090c4ae72685b00b945304`
- field adapter config SHA-256：`b160068f2c7d0e97cdc677725b628a68cd9a9a059d132fbfff1f236c67db8f75`
- field snapshot tree SHA-256：`b239cb1c4ea553a5d012694b4e24a2caf1407c58e422b27a302855484f74988e`

### 6.2 Training behavior

| 候选 | rows | max length | best checkpoint | stop | train loss | best eval loss | peak VRAM | 诊断 |
| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | --- |
| combined | 1,260 / 156 | 768 | 160 | 190/200 | 0.3447 | 0.311614 | 7.415 GB | **CONFIRMED**：160 后 eval loss 连续恶化至 0.313027，早停正常；轻度过拟合迹象 |
| field-conditioned | 7,553 / 935 | 512 | 150 | 180/200 | 0.4330 | 0.283320 | 6.892 GB | **CONFIRMED**：150 后连续无改善，早停正常；不是训练不收敛 |

结论：**INFERRED：训练过程总体稳定，失败不应优先归因于全局欠拟合。** 当前更像数据/合同/评估闭环不足，加上少量泛化硬例失败。

### 6.3 Reproducibility smoke

使用项目 venv 启动全新 Python 进程，加载固定 HF base、combined adapter 和固定 test 第 1 条，结果：JSON 解析、schema、非空、数字、残留语言、cat1 均通过。报告：

`F:\ActionSKUTracker\runtime\temp\qwen_stage4_recovery_adapter_smoke_20260911.json`  
SHA-256：`C7F3FD8FFBCEFB4874FDFA04BB6E311628A9D322170A95339F1401D7B0383A42`

因此：

- `ADAPTER_LOADABLE = PASS`：**CONFIRMED**。
- `ADAPTER_REPRODUCIBILITY = LIMITED_PASS`：**CONFIRMED**，仅证明单样本 fresh-process 可加载和推理；正式 gate 仍需冻结 20–30 条 fixture、重复两次并验证确定性和全部 hash。

---

## 7. Failure Taxonomy

当前最新有效报告中共有 6 条候选硬错误（整行 3、字段 3；可能不是 6 个不同根因类别）。

| SKU / field | failure_type | 证据 | 根因判断 | 推荐动作 |
| --- | --- | --- | --- | --- |
| 3211585 / name | `TECH_TOKEN_ERROR`、`FACT_OMISSION`；同时疑似 `GOLD_LABEL_ERROR` | source 含 `Solix SL-300`，预测丢 `300`；gold 却新增 source 未见的 `True` | 模型丢技术 token 为 **CONFIRMED**；gold 新增 `True` 的合法性 **UNKNOWN** | 先核官网/字典，修 label 后放入 tech-token hard test；不直接训练当前 target |
| 2553956 / name | `SPANISH_RESIDUAL`；疑似品牌/颜色 `AMBIGUOUS_SAMPLE` | source `La Sonata Antracita`，预测保留 `Antracita`；gold 又丢 `La Sonata` | 西语残留 **CONFIRMED**；La Sonata 是否品牌需可信证据 | 人工确认品牌和颜色，再决定 gold；生成品牌+颜色对照 hard case |
| 2523150 / cat1 | `CATEGORY_ERROR`、`MODEL_FAILURE` | source `Juguetes`，gold `玩具`，预测 `手工制作` | **CONFIRMED** 模型输出不属于固定 15 类且语义错误 | 不改源/label；加入类目 hard test，评估是否仅用 deterministic resolver 更合理 |
| 2529028 / spec | `NUMERIC_ERROR`、`UNIT_ERROR`、locale decimal | source `6x25,5x34,5 cm`，预测把 `25,5` 拆成 `25、5` | **CONFIRMED** 模型破坏小数；validator 正确拦截 | 加西语小数逗号与尺寸专门样本；优先规则化再模型化 |
| 3225515 / description | `FACT_OMISSION`、`CHINESE_NAMING_ERROR` | 两处 `2 en 1` 被译为“双面”，数字 1/2 丢失 | **CONFIRMED** 模型错误 | 人工确认修正版；只在非测试同类样本上做 targeted remediation |
| 3221933 / description | `FACT_OMISSION` 或 `ACCEPTANCE_CONTRACT_ERROR` | source/gold 两次出现 7，预测只保留一次 7 | 对当前逐次数字保留合同为 **CONFIRMED FAIL**；是否允许语义去重为 **UNKNOWN** | 冻结合同：本项目要求保留每次出现，则列 MODEL_FAILURE；否则先改 validator 语义再重评 |

另一个已关闭的 evaluator 误判案例：SKU 2562727 的范围 `1–1.5 m²` 曾被旧 policy 报 `NUMERIC_ADDED`，v3 已不再报错。**CONFIRMED**：说明 Stage 4 必须先锁定 validator policy，不能把每次规则变化后的结果继续挂在旧 snapshot 上。

分类汇总（按当前证据，不能强行互斥）：

- model-output failure：至少 4 条明确；另外 2 条含 mixed/contract 问题。
- gold-label/ambiguity：2 条需要确认（3211585、2553956）。
- pipeline/data-builder failure：1 条训练目标截断（3000034），不在上述 6 条 test failure 内。
- environment/adapter load failure：0 条。
- unknown root cause：2 条仍需人工或合同裁决。

---

## 8. Root-Cause Matrix

Stage 4 对每条失败必须按以下顺序判定，前项未通过时不得归因模型：

| 检查层 | 当前状态 | 证据 | 结论/动作 |
| --- | --- | --- | --- |
| Source Fact | 部分未闭合 | 3211585、2553956 需品牌/官网证据 | **UNKNOWN**，先查源 |
| Gold Label | 不足 | test 全为银标；两条 gold 可能增删品牌/词 | **FAIL**，建立独立人审 gold test |
| Sample Placement | 有风险 | product-family overlap；字段 tier 丢失；3000034 截断 | **FAIL**，重建 group split 和 field manifest |
| Train/Infer Contract | 整行不一致 | strict eval prompt 替换训练 prompt | **FAIL**，先统一 contract hash |
| Formatter/Validator | v3 有修正但未冻结 | 2562727 旧误判；snapshot policy 漂移 | **FAIL**，冻结 evaluator + rule fixtures |
| Model Output | 确有错误 | 类目、数字/技术 token、2合1等 | **FAIL**，仅在前五层闭合后纳入 targeted retrain |

因此当前 `primary_blocker` 不是单一模型能力，而是 **ACCEPTANCE + DATA + INFERENCE CONTRACT** 三层共同未闭合。

---

## 9. Scenario Tree

1. **Scenario A — 数据/标签错误**：repair 或 exclude → 重新 hash → 独立人审 → 仅重评；不训练。
2. **Scenario B — split/group leakage**：以 product family/group id 重建三分组 → 旧指标标记 invalid-for-release → 重新评估现有 adapter；不立即训练。
3. **Scenario C — train/infer contract 不一致**：冻结 prompt/chat template/tokenizer/generation contract → 用训练合同重评现有 adapter；若通过，避免重训。
4. **Scenario D — 少量 Hard Cases 仍失败**：从 train/val 之外采集同类但不同 SKU/商品族的人审 Gold → 最小 targeted retrain → 固定 Hard/Temporal test 只评不训。
5. **Scenario E — 单字段系统性失败**：保持 field-conditioned 路线，仅修受影响字段；不重训六字段整行任务。
6. **Scenario F — 全局欠拟合**：只有在干净 group split 上多个字段广泛失败且 train/eval 曲线均未收敛时，才调整训练时长、rank、lr 或覆盖面。当前不满足该条件。
7. **Scenario G — 过拟合**：保留早停，增加多样化 Gold、减少训练或加强分组；当前两轮最佳 checkpoint 后均轻度反弹，应继续用 best checkpoint。
8. **Scenario H — 环境/adapter 问题**：先修绝对 Python/model/tokenizer 路径和依赖 manifest；禁止重训代替环境修复。

---

## 10. Recommended New Stage 4 Architecture

Stage 4 重构为九个可单独验收、前后有硬门禁的子阶段。以下三条是全程不变的设计约束：

1. **Recovery Controller 驱动状态，而不是人工口头推进。** 每一次推进必须留下 manifest、输入哈希、结果和阻断原因；控制器只编排离线训练/评估任务，绝不写 Master、SQLite、正式字典或生产候选。
2. **旧 adapter 的独立 release test 与未来 retrain 的 group split 是两件事。** 旧 adapter 只能在其从未见过的 SKU、source hash 和 product family 上做诊断性复验；未来 group split 只服务于新一轮 targeted retrain 的 train/validation/core-test 隔离，不能倒过来让旧 adapter 获得“未见”资格。
3. **验收的是 Stage 5 实际 Pipeline，不是让 Qwen 成为六字段全能替代品。** 可确定化的类目、数字、单位、型号/技术 token 和格式由规则/字典 resolver 负责；模型仅处理规则无法可靠覆盖的中文命名、自然语言压缩与语义消歧 gap，并且结果仍须经过 Guard 和审核队列。

### 4R — Stage 4 Recovery Controller

新增一个离线 `Stage4RecoveryController`，把恢复过程表示为可恢复、可审计的状态机，而不是由操作者手动判断“下一步”。建议状态为：

`BASELINE_UNFROZEN → EVIDENCE_FROZEN → DATASET_CERTIFIED → CONTRACT_FROZEN → ADAPTER_REEVALUATED → {ACCEPTED | REMEDIATION_REQUIRED | BLOCKED}`

若进入 `REMEDIATION_REQUIRED`，仅在新增人审 Gold、全新 group split 和明确失败假设齐全后，才允许：

`REMEDIATION_REQUIRED → TARGETED_RETRAINED → ADAPTER_REEVALUATED`

每个状态必须写入 `stage4_recovery_state.json`：`state、run_id、entered_at、input_manifest_hashes、adapter_hash、contract_hash、evaluation_set_id、gate_results、blocked_reasons、next_allowed_actions`。任何 gate FAIL/UNKNOWN 必须停在当前状态；`TARGETED_RETRAINED` 不是默认分支；`ACCEPTED` 也仅表示可进入 Stage 5 的离线候选模式。

### 4A — Evidence Freeze

冻结 repo commit、dirty diff、SQLite/字典/normalization 版本、base/tokenizer、脚本、原数据和全部 adapter 哈希；生成只读 inventory。缺任一关键版本则不能进入 4B。

### 4B — Dataset Certification

逐行保留真实 label tier；SOURCE_DAMAGED/AMBIGUOUS/UNVERIFIED 不得升 Gold。人工建立 release test；以 SKU + product family + high-similarity template 分组后重新 split。对每条序列做 token 长度和 target truncation 检查。

**评估集关系必须分开冻结：**

- `legacy_adapter_release_diagnostic`：对当前已训练 adapter，使用它从未见过的 SKU、source hash、product family 的记录。当前新审核的 485 条属于 `MODEL_REVIEWED_SILVER / TEST_ONLY`，可作严格离线诊断和回归测试，但不能冒充人工 release Gold。
- `future_targeted_retrain_group_split`：只在未来确有 confirmed model failure 时，从新的人审 Gold 以 product family/group 重建 train、validation、core-test。该 split 服务于**新 adapter**；旧 adapter 的历史训练集、历史指标和 release diagnostic 不被重写或混合。

两个集合必须有独立 `set_id`、SKU/hash/family 排除清单和 manifest；任一交叉都阻断对应 adapter 的 release claim。

### 4C — Contract Freeze and Parity

生成唯一 `inference_contract.json`，绑定 system/user prompt、字段顺序、chat template hash、special tokens、EOS/BOS/padding、max length、truncation、dtype、generation 参数。训练和评估只能引用同一个 contract id/hash。

### 4D — Adapter Reproducibility

用项目 venv 在 fresh process 上两次加载 exact base/tokenizer/adapter，对固定 20–30 条 fixture 做 greedy inference；验证输出确定、schema 合法、hash 一致、无 Ollama/session 依赖。

### 4E — Evaluation Matrix

同时运行四个评估面：

- Group-held-out Core Test：人工 Gold，按六字段分别统计。
- Hard Test：数字、单位、型号/技术 token、否定、尺寸、品牌、类目、长文本。
- Temporal Test：训练 snapshot 之后首次出现或 source_hash changed 的人工 Gold。
- OOD Test：罕见品类、稀有格式、混合符号和长 details。

此外单列 `Pipeline Resolver Evaluation`：先运行字典/规则层，再只将 resolver gap 交给模型，分别统计规则已闭合率、模型调用率、模型后 Guard 拒绝率、人工接受率和未解决 gap。不得以“Qwen 直接输出六字段”的单一准确率取代该评估。

Exact match 只作诊断；硬事实 gate 独立且不可被平均指标抵消。

### 4F — Failure Closure

每条失败必须拥有 `failure_id、source、gold、prediction、failure_type、root_cause、evidence、action、status`。先排数据/合同/validator，再归因模型；不得保留 UNKNOWN P0。

### 4G — Conditional Remediation

若 4A–4F 后现有 adapter 仍失败：只使用与 test/hard/temporal 不同 SKU 和不同 family 的人工 Gold 做 targeted retrain。优先字段级、最小步数和 early stop。最多两轮，无显著改善即停止。

### 4H — Acceptance and Handoff

所有报告由同一 clean commit 和同一 policy 生成并冻结；Stage 4 acceptance 与 Stage 5 handoff 分开签署。验收对象是 **Dictionary/Rule Resolver → Model Resolver Gap → Guard → Review Queue** 的整条离线 Pipeline；任何生产写入仍保持关闭。

---

## 11. Execution Phases

### Phase 1 — 冻结真实基线（不训练）

- 生成 `environment_manifest`、`asset_inventory` 和 SQLite/dictionary/normalization 版本证据。
- 把当前 v3/v2 evaluator 与旧 snapshot 分开编号；旧报告保留，不覆盖。
- 验收：所有关键输入可通过绝对路径和 SHA-256 唯一定位。

### Phase 2 — 数据与 split 收口（不训练）

- 恢复 1,562 条真实 human/silver tier；修复 field-conditioned tier 传播。
- 对 146 test、250 Hard candidate 和 20 条真正新 silver 做人工分层审核决策。
- 为旧 adapter 冻结从未见过的 SKU/hash/family release diagnostic；当前 485 条仅标为 `MODEL_REVIEWED_SILVER / TEST_ONLY`。
- 生成 product-family group id；只为未来 targeted retrain 重建新的 train/validation/core-test，不回填或混入旧 adapter 的历史训练集。
- 处理 3000034 截断并扫描所有 split。
- 验收：0 exact/group/temporal leakage，release test 100% human-confirmed。

### Phase 3 — 合同和评估收口（不训练）

- 冻结 train/infer contract；先用“训练原 prompt”重评两个现有 adapter。
- 将 strict prompt 作为独立实验，不得冒充训练合同评估。
- 固定 validator fixtures，包括西语小数逗号、范围、重复数字、型号 token。
- 实现 Recovery Controller 的状态、manifest 和 gate transition；由状态机决定唯一允许的下一类离线任务。
- 验收：parity PASS、policy/hash 一致、无 validator 已知误报。

### Phase 4 — 现有 adapter 复验（不训练）

- fresh-process reproducibility 两轮。
- 跑 Core/Hard/Temporal/OOD 四个面和字段级质量矩阵。
- 完成人工四档质量复核和所有 Guard 拒绝项根因归类。
- 若全 gate 通过，直接完成 Stage 4；不训练。

### Phase 5 — 条件式 targeted retrain

- 仅当 Phase 4 留下 confirmed model failure 时启动。
- 为失败类型新增不同 SKU、不同 family 的人审 Gold；Hard/Core/Temporal/OOD 固定不动。
- 字段集中失败则只训字段条件任务；不得默认重训六字段整行模型。
- 最多两次有明确假设的训练；每次只改变一个主变量并生成新 snapshot。

### Phase 6 — 封板

- 全部 gate 在 clean commit、冻结环境、冻结 policy 下重跑。
- 生成 Stage 4 acceptance 和 Stage 5 handoff；仍不启用 production AI。

---

## 12. Acceptance Gates

### STAGE_4_DEFINITION_OF_DONE

| Gate | metric / threshold | 当前状态 | 必需证据 |
| --- | --- | --- | --- |
| DATASET_GATE | 每行谱系字段 100%；Core/Hard/Temporal/OOD test 100% human-confirmed；SOURCE_DAMAGED/AMBIGUOUS/UNVERIFIED 为 0；broken/empty/duplicate 为 0；target truncation 为 0 | **FAIL** | `dataset_audit.json`、逐行 tier、源版本 hash |
| SPLIT_GATE | SKU、source_hash、exact text、product family/high-similarity group 跨 split 均为 0；temporal test 晚于训练 snapshot | **FAIL** | `split_manifest.json` + candidate review |
| TRAIN_INFER_PARITY_GATE | contract id/hash 完全一致；prompt/template/tokenizer/字段/特殊 token/长度/截断/generation 配置逐项 PASS | **FAIL** | `train_infer_parity_report.json` |
| REPRODUCIBILITY_GATE | 两个 fresh process 在固定 20–30 fixtures 上均加载成功；有效 JSON/schema 100%；greedy 输出一致；base/tokenizer/adapter hash 完全匹配 | **LIMITED_PASS** | `reproducibility_report.json` |
| HARD_FACT_GATE | Core + Hard + Temporal + OOD 上 `NUMERIC_MISMATCH、UNIT_MISMATCH、TECH_TOKEN_CORRUPTION、FACT_HALLUCINATION、SOURCE_FACT_LOSS` 合计 0；无隐式例外 | **FAIL** | 四类测试报告 + failure rows |
| STRUCTURE_GATE | JSON/schema 100%；目标必填完整 100%；cat1 合法率 100%；普通西语/英语残留 0（已登记品牌/型号白名单除外） | **FAIL** | 字段级 evaluation matrix |
| FIELD_QUALITY_GATE | 每字段人工复核覆盖 100%；`ACCEPT_AS_IS + ACCEPT_WITH_MINOR_EDIT >= 98%`；`REQUIRES_MAJOR_EDIT <= 2%`；`REJECT = 0` | **UNKNOWN** | 分层人工 review CSV + summary |
| PIPELINE_GATE | Dictionary/Rule Resolver 先执行；模型只接收已登记的 resolver gap；规则闭合字段不得被模型改写；模型结果 Guard + Review Queue 覆盖率 100% | **FAIL** | `pipeline_evaluation.json`、gap sample、规则/模型分层指标 |
| FAILURE_CLOSURE_GATE | 全部自动/人工失败 100% 有根因和处置；未关闭 P0=0；UNKNOWN P0=0 | **FAIL** | `failure_report.csv` |
| ARTIFACT_GATE | 所有 manifest/report 由同一 clean commit、同一 policy 和同一 environment 生成；SHA 完整；生产开关保持关闭 | **FAIL** | Stage 4 manifest 与 git evidence |

任何一项 FAIL 都不能以平均准确率、低 eval loss 或“基本可用”替代。

---

## 13. Stage 5 Entry Gate

### Stage 5 可以做什么

- 只对 `NEW`、`source_hash changed`、`NEEDS_REVIEW` 做离线候选生成。
- 先走 Dictionary/Rule Resolver：一级类目、数字、单位、尺寸、型号/技术 token、固定格式等可确定事实必须在此层闭合，且不得被模型重写。
- 只把 resolver gap 交给已通过 Stage 4 的 adapter，例如中文命名压缩、自然语言表述、品牌与商品名组合、上下文语义消歧。
- 输出候选、Guard 结果和审核队列；连续多个独立批次统计 Resolver Gap Accuracy。
- 允许人工接受、微调或拒绝候选，但仍不自动写 PRIMARY。

### Stage 5 不能承担什么债务

- 不修训练数据、group leakage 或 label tier。
- 不修 train/infer mismatch。
- 不调查 adapter 为什么加载失败。
- 不补 Stage 4 失败样本根因。
- 不以生产反馈代替 Core/Hard/Temporal/OOD test。

### STAGE_5_SHADOW_ENTRY_GATE

下列条件允许启动**离线 shadow**，而不宣称模型通过 Stage 4，也不允许任何生产写入：

1. 唯一 adapter、base/tokenizer、inference contract/policy 均已显式指定；禁止“最新目录自动选择”。
2. 输入只来自 `NEW`、`source_hash changed`、`NEEDS_REVIEW` 或固定测试 fixture，输出只落入隔离候选文件。
3. Dictionary/Rule Resolver 必须先执行；其已闭合字段不得被模型输入或改写。
4. 每个模型 gap 字段必须通过 Guard，失败项必须进入 Review Required，不能降级为候选成功。
5. Master、SQLite、正式字典、生产翻译队列和 production AI 开关均保持不写/关闭。

### STAGE_5_RELEASE_ENTRY_GATE

下列条件才允许把 shadow 结果扩展为连续日批次的正式候选链：

1. Stage 4 九个 gate 全 PASS，且 acceptance 文件已冻结。
2. 选定唯一 adapter id、base/tokenizer/contract/policy hash；禁止“最新目录自动选择”。
3. Core、Hard、Temporal、OOD 都有不可变测试清单和报告，hard factual errors 为 0。
4. 生产开关仍关闭，写入目标仅为隔离候选/Review Queue。
5. Stage 5 至少设计 3 个相互独立的日批次；每批记录 resolver coverage、gap count、model accept/minor/major/reject、Guard reject。
6. 任一批出现 P0 事实错误，立即停止 Stage 5，回到 Stage 4 failure closure；不得在线修补后继续。

---

## 14. Rollback / Stop Conditions

- 数据质量、split、parity 或 reproducibility 任一 gate FAIL：禁止训练。
- 同一种配置、同一数据、同一假设不允许无变化重复训练。
- 每次训练只能改变一个主变量；没有 before/after Hard/Temporal 对比则该轮无效。
- 连续两次 targeted retrain 后 Hard Test 无显著改善，或任何新 P0 增加：`STOP → root-cause reassessment`。
- hard factual errors 未降为 0：不得进入 Stage 5 的 release/prod-candidate 模式；仍可运行不写入的 shadow 以定位 Guard 与 resolver gap。
- adapter/base/tokenizer/hash 不匹配，或环境落到系统 Python/CPU fallback：立即停止。
- 任何脚本尝试写 Master、SQLite PRIMARY、字典正式值或打开 production AI：立即停止并回滚到只读模式。
- 新 split 建立后，旧 Stage 4 指标保留作历史对照，但必须标记 `INVALID_FOR_RELEASE_DECISION`，不得删除。

---

## 15. Expected Artifacts

恢复完成后至少应有以下等价产物；已有文件可复用，但必须补齐版本、hash 和状态，不为改名重复造文件：

1. `stage4_manifest.json`
2. `training_snapshot_manifest.json`
3. `dataset_audit.json` 和逐行审计清单
4. `split_manifest.json`（含 group/temporal leakage）
5. `training_config.json`
6. `environment_manifest.json`
7. `inference_contract.json`
8. `train_infer_parity_report.json`
9. `evaluation_report.json`（六字段矩阵）
10. `failure_report.csv`
11. `hard_test_report.json`
12. `temporal_test_report.json`
13. `ood_test_report.json`
14. `adapter_manifest.json`
15. `reproducibility_report.json`
16. `STAGE_4_ACCEPTANCE.md`
17. `STAGE_5_HANDOFF.md`

每个产物都必须记录：artifact version、created_at、git commit、worktree clean、输入/输出 SHA-256、policy/contract/environment id、只读/写入范围。

---

## 16. Commands to Execute Later

以下命令是恢复执行阶段的建议顺序，本 Planning 轮没有执行训练命令。所有 Python 命令必须用项目 venv 绝对路径。

### 16.1 只读基线确认

```powershell
Set-Location -LiteralPath 'F:\ActionSKUTracker'
git status --short --branch
git rev-parse HEAD
nvidia-smi
& 'F:\ActionSKUTracker\runtime\training\qwen3_8b\.venv\Scripts\python.exe' -c "import torch, transformers, peft, trl, bitsandbytes, accelerate; print(torch.__version__, torch.version.cuda, transformers.__version__, peft.__version__, trl.__version__)"
```

### 16.2 需要先实现的恢复审计入口

下列脚本是本计划建议的新入口，**当前不存在，不应现在假装执行成功**：

```powershell
& 'F:\ActionSKUTracker\runtime\training\qwen3_8b\.venv\Scripts\python.exe' scripts\audit_qwen_stage4_lineage.py --config config\qwen_stage4_recovery.yaml --read-only
& 'F:\ActionSKUTracker\runtime\training\qwen3_8b\.venv\Scripts\python.exe' scripts\audit_qwen_group_split.py --manifest runtime\training\qwen3_8b\stage4_recovery\dataset_manifest.json
& 'F:\ActionSKUTracker\runtime\training\qwen3_8b\.venv\Scripts\python.exe' scripts\verify_qwen_train_infer_parity.py --snapshot runtime\training\qwen3_8b\stage4_recovery\snapshot_manifest.json
& 'F:\ActionSKUTracker\runtime\training\qwen3_8b\.venv\Scripts\python.exe' scripts\verify_qwen_adapter_reproducibility.py --fixtures runtime\training\qwen3_8b\stage4_recovery\repro_fixtures.jsonl --repeat 2
```

### 16.3 现有 adapter 的同合同重评

在新 contract 和 group-held-out test 冻结后，先重评，不训练：

```powershell
& 'F:\ActionSKUTracker\runtime\training\qwen3_8b\.venv\Scripts\python.exe' scripts\benchmark_qwen_four_way.py `
  --model-path 'F:\ActionSKUTracker\runtime\models\Qwen3-8B' `
  --old-adapter 'F:\ActionSKUTracker\runtime\training\qwen3_8b\20260908\baseline_gold_qlora_8b_20260909_clean\adapter' `
  --current-adapter 'F:\ActionSKUTracker\runtime\training\qwen3_8b\20260911\combined_gold_incremental\formal_qlora_200_earlystop\adapter' `
  --test-file 'F:\ActionSKUTracker\runtime\training\qwen3_8b\stage4_recovery\core_test.jsonl' `
  --output 'F:\ActionSKUTracker\runtime\training\qwen3_8b\stage4_recovery\core_benchmark.json'
```

注意：当前 adapter 按原训练 prompt 重评时不要添加 `--strict-prompt`。若未来决定采用 strict contract，应先用该 contract 重建训练数据并生成新 adapter，不能将 prompt 替换后的报告与旧 snapshot 混为一体。

### 16.4 条件式 targeted retrain

只有 4A–4F 全 PASS、且 failure report 明确要求模型修复时，才根据冻结配置执行；命令必须引用新版本数据和新输出目录，不覆盖现有 adapter/checkpoint：

```powershell
& 'F:\ActionSKUTracker\runtime\training\qwen3_8b\.venv\Scripts\python.exe' scripts\train_qwen_qlora.py `
  --model-path 'F:\ActionSKUTracker\runtime\models\Qwen3-8B' `
  --train-file 'F:\ActionSKUTracker\runtime\training\qwen3_8b\stage4_recovery\targeted_train.jsonl' `
  --eval-file 'F:\ActionSKUTracker\runtime\training\qwen3_8b\stage4_recovery\targeted_validation.jsonl' `
  --output-dir 'F:\ActionSKUTracker\runtime\training\qwen3_8b\stage4_recovery\targeted_qlora_v1' `
  --max-length 512 --max-steps 200 --short-run --early-stopping-patience 3
```

实际参数必须来自已审核的 `training_config.json`；上例只表达命令形态，不是立即训练授权。

---

## QWEN STAGE 4 RECOVERY AUDIT — Final Summary

```text
CURRENT STATE

repo: F:\ActionSKUTracker
branch: feat/export-foundation-v1
head: 1dc2fc0338c2b6ace73b4869b11a0ebfd1179c35
worktree: DIRTY

base_model: F:\ActionSKUTracker\runtime\models\Qwen3-8B
adapter: combined_current + field_conditioned_current (both offline Stage 4 FAIL)
dataset: 1,562 rows = 31 human-confirmed/reviewed + 1,531 model-reviewed silver
environment: project venv PASS; RTX 3060 12 GB; CUDA 12.6

CURRENT STAGE 4

original_goal: frozen-data QLoRA with hard-fact and human-fidelity gates
actual_state: training/load works; trustworthy release evaluation is not closed
primary_blocker: DATA + TRAIN/INFER CONTRACT + ACCEPTANCE

DATA

total_samples: 1,562 active combined rows
gold: 31 human-confirmed/reviewed by metadata
silver: 1,531
unverified: 0 inside active combined set, but lineage versions remain incomplete
invalid: 126 source-blocked outside active set; 2/22 latest candidates blocked
duplicates: exact duplicate/leakage 0
leakage: product-family leakage candidates FOUND; temporal leakage UNKNOWN

ADAPTER

loadable: PASS
reproducible: LIMITED_PASS (fresh-process one-fixture smoke)
base_model_match: PASS
tokenizer_match: PASS for frozen snapshot files

TRAIN / INFER

contract_parity: FAIL
chat_template_parity: PARTIAL PASS
field_contract_parity: PARTIAL PASS; one truncated train target and tier loss

FAILURES

current hard-failure records: 6 (3 row-level + 3 field-conditioned)
data/label failure: at least 2 mixed cases require confirmation
pipeline failure: 1 confirmed truncated training target
model failure: at least 4 confirmed; 2 mixed/contract cases
environment failure: 0 current
unknown: source/label decision remains for 2 cases; temporal/OOD quality unknown

P0: trustworthy human test, group split, contract parity, zero hard errors,
    Hard/Temporal/OOD and failure closure, full data lineage
P1: snapshot-policy drift, field tier loss, target truncation,
    environment manifest/log completeness, dtype parity
P2: richer localization quality diagnostics and wider OOD coverage

RECOMMENDED STAGE 4

phases: evidence freeze -> dataset certification/group split -> contract freeze ->
        existing-adapter re-evaluation -> conditional targeted retrain -> acceptance

STAGE 4 DEFINITION OF DONE

gates: DATASET, SPLIT, TRAIN_INFER_PARITY, REPRODUCIBILITY, HARD_FACT,
       STRUCTURE, FIELD_QUALITY, FAILURE_CLOSURE, ARTIFACT all PASS

STAGE 5 ENTRY GATE

requirements: frozen passing Stage 4 package; unique adapter/contract/policy;
              zero hard factual errors; isolated candidate-only writes;
              three independent offline batches; immediate rollback on P0

RETRAIN REQUIRED: CONDITIONAL

if retrain:
why: only confirmed model failures remaining after data/contract/validator closure
scope: field-specific, minimal, human-Gold, no test-family overlap
target: current confirmed failure taxonomy, not broad corpus repetition

STOP CONDITIONS

data/parity/repro FAIL => no training
same unchanged configuration => no rerun
two targeted retrains without Hard Test improvement => stop and reassess
any PRIMARY/production write => stop

PLANNING VERDICT

READY_TO_EXECUTE_STAGE4_RECOVERY: YES
CURRENT_MODEL_READY_FOR_STAGE5_RELEASE: NO
```

## 18. Stage 5 Shadow Bootstrap（2026-09-12）

**状态：`SHADOW_BOOTSTRAP_PASS / RELEASE_NOT_AUTHORIZED`。** 本节只记录实际执行的离线 Pipeline，不改变 Stage 4 的 FAIL 结论，也不授予训练或生产权限。

- 入口：`scripts/qwen_offline_stage5.py`。
- 顺序：`Dictionary/Rule Resolver → 单字段 Model Resolver Gap → model_guard → 隔离候选 JSONL`。
- 规则层仅关闭 `manual_override`、哈希匹配的 `product_dictionary/model_cache`、以及当前西语类目对应的 `category_dictionary`；过期产品值不得关闭 gap。
- 模型只能收到未关闭字段的单字段西语源文本。参考译文即使存在于测试 fixture，也不会进入模型 prompt。
- 每条候选同时记录 `rule_resolved_fields`、`model_gap_fields`、Guard 原因和 `source_hash`，便于人工复核。
- 输出没有任何 Master、SQLite、字典或生产候选写入路径。

首次 smoke 使用冻结的 `stage4_test_only_485.jsonl` 和固定的 combined adapter：

| smoke | rows | 规则关闭字段 | 模型 gap 字段 | Guard 接受 | Review Required | 生产写入 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `stage5_rule_first_smoke1` | 1 | 4 | 2 | 1 | 0 | 否 |
| `stage5_rule_first_smoke2` | 2 | 8 | 4 | 2 | 0 | 否 |

第 2 条 SKU `3212435` 是旧的整行提示词评估曾出现 `NUMERIC_DROPPED` 的样本。新 shadow 的单字段模型路径完整保留了同字段数字并通过 Guard；这只能证明 **Pipeline 的规则优先/单字段协议可工作**，不能倒推旧整行 adapter 已通过 Stage 4，也不能取代 Hard/Temporal/OOD 评估或人工验收。

随后以当前 SQLite `CURRENT` 集合中未被历史训练/审核消费的 20 条候选做真实离线批次：120 个字段中规则/字典闭合 36 个，84 个缺口发给模型；19 条通过 Guard，1 条（SKU `3215546`）因描述数字遗漏进入 `REVIEW_REQUIRED`。二次分类审查确认所有模型 prediction 都是对应 gap 的子集、没有覆盖规则字段；规则值事实校验失败为 0；`master_written=false`、`dictionary_written=false`、`sqlite_written=false`。候选收集本身还排除了 5,343 条已消费 SKU、4 条源阻断和不完整/Guard 不通过源，产物为：

`runtime/training/qwen3_8b/20260912/qwen_incremental_stage5_shadow_candidate_20.jsonl` → `qwen_incremental_stage5_shadow_candidate_20_stage5.jsonl` 及其 manifest。

下一步是以三个互相独立的离线批次统计：规则关闭率、模型 gap 数、Guard 拒绝率及人工接受/微调/拒绝率；任何 P0 事实错误仍按既定 stop condition 回到 Stage 4 failure closure。
