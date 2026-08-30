# Knowledge Production V1 验收清单

## 已完成

- 统一状态机、六个中文字段和六字段 source hash 合同；
- SQLite resolution、queue、candidate、approval audit 表；
- 字段级 Resolver、增量队列去重、SOURCE_BLOCKED 排除；
- Candidate Validator、字段级 Shadow Auto-Approval；
- 默认 feature gates 全部关闭，未触碰 Presence/Lifecycle/Export 主链。

## 必须通过的测试

人工覆盖优先、源事实不可变、hash 变化失效、队列去重、SOURCE_BLOCKED 不入队、
候选污染拒绝、自动批准关闭/Shadow、高风险字段阻断、人工冲突阻断、幂等解析。

## 未完成

生产 Apply、真实 AI provider、scoped dictionary 审批 UI/CLI、Primary 切换和真实数据分级上线。
