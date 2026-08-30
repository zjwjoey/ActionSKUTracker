# AI Translation Candidate V1 合同

模型只生成 Candidate，不直接更新 `product_localizations`。请求只包含完成本次字段所需的
西语事实和已批准术语；必须记录 `model_provider、model_name、prompt_version、source_hash`。

Validator 检查 schema、字段长度、URL/SKU/价格污染、source hash 和非法空值；失败为
`AI_REJECTED`。source hash 相同且验证通过的结果可按 cache key 复用：
`sku + source_hash + prompt_version + model_family`。当前 `translation.ai_enabled=false`。
