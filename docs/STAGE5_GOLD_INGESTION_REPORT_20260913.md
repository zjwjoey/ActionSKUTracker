# Stage 5 Gold 接入报告

日期：2026-09-13  
输入：`Action_Stage5_候选数据_500条_修订版_446Gold.xlsx`  
性质：离线证据接入与合同预检，不是生产 Apply，也不是模型训练授权。

## 结果

| 项目 | 结果 |
| --- | ---: |
| 工作簿行数 | 500 |
| 唯一 SKU | 500 |
| 人工确认 Gold | 446 |
| 修订后待 Owner 确认 | 45 |
| 明确非 Gold | 9 |
| 历史 split 重叠 | 442 |
| 新的、无历史重叠 Gold | 4 |
| 六个西语事实字段 mismatch | 0 |
| source_hash mismatch | 0 |
| 生产写入 | 关闭 |

4 条无历史重叠的 Gold SKU：`3215954、3218150、3221215、3221470`。

## 合同预检

由 Gold 的六个西语事实字段生成 source-only 输入（仅一条 `user` 消息，无 assistant/reference 答案），通过 Stage 5 预检：

- 446 行
- 2676 个字段计划
- 911 个模型请求计划
- frozen model/tokenizer/adapter/inference contract 身份校验通过
- Stage 4 状态仍为 `READY_FOR_STAGE5_OFFLINE_SHADOW`

系统 Python 的 `torch.cuda.is_available()` 为 `False`，但项目专用训练虚拟环境使用 CUDA 12.6；冻结 Stage 5 合同明确禁止 CPU fallback，因此实际推理只通过项目虚拟环境执行。

已对 4 条无历史重叠 Gold 做真实冻结模型 shadow（16 个模型字段请求）。结果为 16/16 Guard PASS、0 个事实逃逸、0 个 pipeline error；与人工 Gold 逐字段比较为 11 条完全一致、13 条不一致（描述 4、详情 4、品名 3、规格 2）。这些差异全部保留为人工复核，不自动覆盖 Gold，也不进入生产。

比较报告：
`F:\ActionSKUTracker\runtime\stage5\20260913\gold_shadow_disjoint4_qwen_batch16_v1\stage5_shadow_gold_comparison.json`

为保留合同一致性，入口新增了可选 `--batch-size`；默认值仍为 `1`，batch=16/32 在同一批 4 条样本上与串行 16 个模型输出完全一致。全量结果仍必须经过人工复核，不能直接 Apply。

随后使用 batch=32 完成了 446 条全量冻结模型 shadow：2676 个字段计划、911 个模型请求；852 个 Guard PASS、59 个 Guard REJECT（数字 7、单位 31、技术 token 24、残留语言 4），0 个事实错误逃逸，0 个 pipeline error，0 个重复。与人工 Gold 逐字段对账：1630 条完全一致，1652 条去空白后一致，1046 条需要人工复核；结果为 `REVIEW_REQUIRED_NO_APPLY`，未覆盖人工 Gold。

全量 shadow 对账：
`F:\ActionSKUTracker\runtime\stage5\20260913\gold_shadow_full_qwen_batch32_v1\stage5_shadow_gold_comparison.json`

## 证据

主 manifest：
`F:\ActionSKUTracker\runtime\training\qwen3_8b\20260913\stage5_gold_ingestion_446_v2\stage5_gold_ingestion_446_v1.manifest.json`

接入脚本：
`F:\ActionSKUTracker\scripts\ingest_stage5_gold_workbook.mjs`

## 当前边界

442 条历史重叠记录不重复训练；4 条新 Gold 只进入未来 split 候选，尚未启动训练。Stage 4 `FULL_STAGE4_RELEASE=false`，因此正式 Stage 5 Acceptance、模型训练授权和生产 Apply 仍然关闭。
