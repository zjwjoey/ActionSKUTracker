# Stage 6 Definition Audit

审计日期：2026-09-12  
结论：`STAGE_6_DEFINITION_STATUS = NOT_FROZEN`

## 搜索范围

已搜索 `docs/`、`README.md`、`config/`、`tests/`、`scripts/` 和训练规划中与 `Stage 6`、`STAGE_6`、`STAGE6` 有关的内容。

仓库只找到 Stage 4、Stage 5 离线 shadow 和既有 Dictionary Apply Gate 的描述，没有找到以下任一正式证据：

- Stage 6 的冻结目标、输入输出和禁止项；
- Stage 6 Acceptance 或 Entry Gate；
- Stage 5 到 Stage 6 的签署 Handoff；
- 对应 config contract、测试或状态机状态；
- 项目所有者确认的 Stage 6 质量阈值。

因此不得把“生产应用”“继续训练”“自动写 Master”或“放宽人工审核”擅自定义为 Stage 6。

## 可复用的既有要求

以下内容已经存在，但它们不等于 Stage 6 定义：

1. Stage 5 是离线候选层，不写 PRIMARY、Master、正式字典、生命周期、价格或 availability。
2. Dictionary Apply Gate 已实现，默认 `dictionary_apply.production_enabled: false`。
3. 既有 Apply Gate 要求字典与已发布基线逐文件 hash 一致、审计未过期、SKU 满足准入状态，且默认不接受 PROVISIONAL 品牌。
4. 人工覆盖是字段级保护，不能因为某个字段人工确认就冻结同一 SKU 的全部字段。
5. 官方西语数据是事实层；字典和模型不能反向改写官网事实。

## 审计结论

- 不生成 `STAGE_6_HANDOFF.md`，因为这会虚构已满足的正式准入。
- 生成独立草案 `STAGE_6_ENTRY_CONTRACT_DRAFT.md` 供项目所有者确认。
- 草案中的每条规则必须标记为 `EXISTING_REQUIREMENT`、`INFERRED_REQUIREMENT` 或 `NEW_PROPOSAL`。
- 在草案被确认和冻结前，Stage 6 verdict 只能是 `NOT_READY_FOR_STAGE6`。

