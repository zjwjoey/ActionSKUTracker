# Translation Auto-Approval V1 合同

默认关闭。自动批准是规则引擎，不是单看模型 confidence。必须同时满足：源有效、hash 未变、
Validator PASS、无人审冲突、无 scoped/category 冲突、关键术语已批准、confidence 达标。

第一阶段只允许低风险字段（类目、批准术语替换、简单规格）；描述和详情永不自动批准。
Shadow 模式只产生 `WOULD_AUTO_APPROVE` 审计，不写正式 localization。策略版本固定记录为
`auto_approval_v1`，配置 `translation.auto_approval_enabled=false` 可立即熔断。
