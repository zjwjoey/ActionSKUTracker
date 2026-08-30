# Incremental Translation Queue V1 合同

只有 NEW、source hash 变化、缺少正式 localization 或明确审核问题才入队；
`SOURCE_BLOCKED` 永不入 AI 队列。队列去重键为：
`sku + source_hash + requested_fields`。

优先级：P0 新 SKU 标题/类目缺失，P1 source hash 变化，P2 规格/描述/详情缺失，
P3 低价值复核。模型失败只进入重试或失败状态，不回滚当天商品事实提交。
