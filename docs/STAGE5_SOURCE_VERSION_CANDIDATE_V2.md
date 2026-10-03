# Stage 5 Source-Version Candidate Queue v2

本版本将候选定义为 source-version candidate，不是训练集，也不是 Gold。
输出目录由 `scripts/select_stage5_source_versions_v2.py` 生成：

`runtime/training/qwen3_8b/20260913/stage5_new_source_versions_999/`

## 固定边界

- 目标数量：999 条；不把 2026-09-11 详情阶段被拦截的记录补回。
- 允许旧 SKU 的新源版本进入，但状态必须是 `SOURCE_HASH_CHANGED`，不得标为新品。
- 真正未出现在历史训练 SKU 的记录状态为 `NEW_SKU_NEW_SOURCE`。
- `(SKU, source_hash)` 必须与历史 train/validation/test 完全不重叠。
- 来源 run 必须 QA PASS、FULL_COMMIT、完整观测、Presence FULL、最终 NORMAL。
- 只产生 source-only JSONL；不写 Master、SQLite、字典、模型 adapter 或生产状态。

## 血缘字段

每条记录保存 source run、快照路径、观察时间、V2 semantic hash 合同、SKU/产品族历史关系、冻结测试集关系、source consistency 状态和候选状态。产品族使用确定性 `SPANISH_NAME_HEURISTIC_V1`；它用于后续 split 隔离，不用于改写官网事实。

## Source consistency

对数字、单位、产品对象、材质、尺寸/范围执行保守检测。检测到冲突只进入 `SOURCE_CONFLICT_REVIEW`，不自动修改或生成 Gold。

## Hash 合同

继续使用现有兼容合同：

```text
source_hash_algorithm = semantic_localization_source_hash_v2
source_hash_contract_version = SOURCE_HASH_V2
```

V2 只规范详情字段的分隔符、空白和已知无冒号商品编号写法，不改变产品事实语义；读取旧 V1 产物时保留兼容匹配，但新生成的候选、审核和预览统一写 V2，避免把格式变化误判为源事实变化。

## 验收

最终 audit 必须满足：999 条、999 个唯一 source-pair、快照逐字段匹配 999/999、hash 重算 999/999、历史 source-pair 重叠 0、2026-09-11 异常记录 0、生产写入 0、训练运行 0。通过后才进入人工中文 Gold 审核；实际训练数量允许小于 999。
