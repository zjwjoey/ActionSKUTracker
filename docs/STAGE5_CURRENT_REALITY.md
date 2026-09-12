# Stage 5 Current Reality

审计日期：2026-09-12

仓库：`F:\ActionSKUTracker`

目标分支：`feat/export-foundation-v1`
审计性质：修改前只读 Reality Audit

## 1. Git 与冻结证据

| 项目 | 真实状态 | 证据 |
| --- | --- | --- |
| 当前分支 | IMPLEMENTED | `feat/export-foundation-v1` |
| HEAD / origin 同步 | IMPLEMENTED | 本地与 `origin/feat/export-foundation-v1` 均为 `1dc2fc0338c2b6ace73b4869b11a0ebfd1179c35` |
| clean worktree | MISSING | 工作区含多项 Qwen 相关修改和历史未跟踪产物 |
| Stage 4 Acceptance | MISSING | 未发现独立、正式、通过的 Stage 4 Acceptance 文件 |
| Stage 4 recovery state | PARTIAL | `READY_FOR_STAGE5_OFFLINE_SHADOW`，但 `FULL_STAGE4_RELEASE=false` |
| Stage 5 handoff | MISSING | 未发现正式 Stage 5 handoff |
| Stage 6 正式定义 | MISSING | docs、README、config、tests、scripts 中均未检出正式定义 |

Stage 4 evidence freeze 明确写明：只授权离线 shadow，不授权训练或生产写入。其阻断原因包括 20 条诊断的数字保持率为 `0.991666...`、15/500 严格候选被排除，以及当前 485 条集合为模型复核银标而非人工 Gold。

## 2. 当前冻结身份

| 对象 | 当前证据 |
| --- | --- |
| Base model config SHA-256 | `f7c4eadfbbf522470667b797a3c89be2524832d2d599797248dc304fff447c30` |
| Tokenizer aggregate SHA-256 | `20b2e9e42943ba42526158348b44d22714d2629321fc4feaaa3f1277516d2aa9` |
| Combined adapter tree SHA-256 | `dabc9294fa553d60eddb3a1a0c8939117b2bfefd8527d2f1742bdd6ccfbb1d0c` |
| Stage 4 inference contract SHA-256 | `a8d1328be1855e86e0461430911da6beedcdb3081a5dfc1e46b8b8793920720c` |
| 当前 Stage 5 entrypoint SHA-256 | `945be0bb22d36531a0aa5e114a6b048bc9afe8638124cb952f9d4f1bc7a05a3e` |
| 当前 model Guard SHA-256 | `2993e7b82e7a0a35e6cdc5ded1d96e46fd0b159b1fff0058c9fceb940ab1fc6b` |
| Stage 4 快照 evaluator SHA-256 | `abc26f834041aebb1c6097988060a5872b66c3941fc99f1e285486fe3ccefbf2` |
| 当前 evaluator SHA-256 | `fe86a9bef7ae61af6e39d52498d4f036ee1cf90602aab577d652841c3dbd5ba4` |

当前 evaluator 与 Stage 4 快照记录不一致。冻结快照本身没有被改写，但当前工作区代码不能被宣称为该快照中的 evaluator。Stage 5 必须建立自己的显式 policy/entrypoint 绑定，并保持对 Stage 4 adapter、tokenizer、contract 的引用；不得静默沿用文件名。

## 3. 当前真实调用链

```text
任意 JSONL
  -> qwen_offline_stage5.py
  -> Dictionary Resolver
  -> READY 且 hash 安全的字段闭合
  -> 剩余字段调用 frozen-path Qwen（路径仍可由 CLI 覆盖）
  -> model_guard 数字/语言/schema 检查
  -> JSONL candidate + 简单 manifest
```

该脚本没有被 daily orchestrator、Presence、Lifecycle、价格历史或正式 Export 调用，因此当前不会影响每日采集主链。

## 4. 能力矩阵

| 能力 | 状态 | 审计结论 |
| --- | --- | --- |
| Dictionary Resolver 优先 | IMPLEMENTED | manual/product/category/term/model-cache 的 READY 字段可先闭合 |
| 仅把 resolver gap 交给 Qwen | IMPLEMENTED | 当前按字段调用模型 |
| 字典结果事实 Guard | IMPLEMENTED | 不安全字典值会重新打开为 gap |
| NEW 检测 | PARTIAL | 现有 collector/knowledge queue 能识别，但 Stage 5 入口不验证 |
| source_hash changed 检测 | PARTIAL | resolver/queue 能识别，Stage 5 输入未强制绑定当前事实 |
| NEEDS_REVIEW 检测 | PARTIAL | 上游存在，Stage 5 接受任意 JSONL |
| localization/resolver gap | IMPLEMENTED | 字段级 gap 已存在 |
| 固定 model/adapter/tokenizer/contract | WRONG_CONTRACT | CLI 可改 model/adapter；没有完整 hash preflight |
| prompt/chat template/generation freeze | WRONG_CONTRACT | prompt 位于 evaluator 脚本常量中，未绑定 contract hash |
| JSON/schema Guard | PARTIAL | 有基本校验，但空输出、完整 provenance 尚未闭合 |
| Numeric Guard | IMPLEMENTED | 同字段、重复数字、窄范围 `un/una` 等价已覆盖 |
| Unit Guard | MISSING | 数字相同但单位被替换时可能通过 |
| Technical-token Guard | MISSING | USB-C、IP44、G12、E27 等缺失/篡改未形成独立 Gate |
| Category Guard | PARTIAL |评估器有固定一级类目检查，Stage 5 candidate Guard 未独立执行 |
| Language residual Guard | IMPLEMENTED | 西语/英语常见词残留会拒绝；词表仍是保守规则 |
| Candidate provenance | PARTIAL | 有 source、prediction、rule evidence，但缺 candidate_id、batch/run、模型与 policy hashes、raw output、review timestamps 等 |
| Candidate Store | PARTIAL | 隔离 JSONL 已有，但非原子、无不可变批次合同 |
| Review Queue | MISSING | Guard reject 仅标记 `REVIEW_REQUIRED`，没有生成统一 Stage 5 审核文件 |
| Failure closure | MISSING | 没有标准 failure report 与根因分层 |
| Replay / idempotency | MISSING | 无 deterministic candidate ID、重复防护或 replay 对账 |
| Batch manifest | PARTIAL | 只记录路径和汇总，缺输入/输出/config/policy/environment hashes |
| 三独立批次评估 | MISSING | 现有 1/2-row smoke 和一个 20-row batch 不构成三个独立正式 batch；9/11 与 9/12 输入还存在 SKU 重叠 |
| 人工审核状态 | SAFE/PARTIAL | 目前没有伪造人工确认，但也没有正式 manual review queue 合同 |
| 生产写入 | SAFE | 当前 Stage 5 脚本只写指定 JSONL/manifest，不写 Master、PRIMARY、字典或 localization |

## 5. 当前已知运行证据

`stage5_rule_first_smoke_current`：1 行，4 个规则闭合字段、2 个模型 gap、Guard 接受 1、Review 0、生产写入 0。

`20260912` shadow batch：20 行，36 个规则闭合字段、84 个模型 gap、Guard 接受 19、拒绝 1；拒绝原因为数字遗漏。该错误在进入可接受候选前被 Guard 正确拦截，因此是 `GUARD_REJECT`，不是事实错误逃逸。

## 6. 直接写入审计

当前 `qwen_offline_stage5.py` 只写调用者指定的 candidate JSONL 和相邻 manifest。它没有调用 `KnowledgeStore`，没有写 SQLite PRIMARY、Master、production dictionary、production localization、Lifecycle 或价格事实。

仓库另有 `KnowledgeStore`，可以写 translation preview/audit 表，且构造函数允许 `PRIMARY` role；当前 Stage 5 没有调用它。正式 Stage 5 设计仍应完全不依赖该入口，以防未来误接生产库。

## 7. 审计结论

- 当前 Stage 5 是可用的离线 shadow 原型，不是可接受的正式 Stage 5 Pipeline。
- 没有发现已通过的 Stage 4 Acceptance；现有正式状态明确为 `FULL_STAGE4_RELEASE=false`。
- 在不改 Stage 4 数据、测试或 adapter 的前提下，可以继续完成 Stage 5 的工程合同、完整 Guard、provenance、不可变 manifest、Review Queue、failure closure、replay 和三批 shadow 验证。
- 即使工程 Gate 全部完成，正式 Stage 5 verdict 仍必须受 Stage 4 release gate 和真实人工审核约束。
- 当前 Stage 6 没有冻结定义：`STAGE_6_DEFINITION_STATUS = NOT_FROZEN`。
