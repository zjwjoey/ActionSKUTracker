# Knowledge Production V1 架构合同

本层负责把 SQLite 中的西语事实按 SKU 增量解析为中文派生字段。它不参与
Presence、Lifecycle、价格、链接或图片判断，也不重新访问官网。

## 数据流

```text
products (Spanish facts)
  -> source_hash
  -> field-level resolver
  -> resolution / review / translation queue
  -> candidate validator
  -> human or policy approval
  -> product_localizations (Chinese production data)
  -> export
```

`name_zh、cat1_zh、cat2_zh、spec_zh、desc_zh、details_zh` 是唯一可写字段。
SKU、canonical_id、所有西语事实、价格、链接和生命周期字段只读。

当前实现阶段：合同、状态机、SQLite 表结构和离线纯函数已落地；
`knowledge.production_apply_enabled=false`、`translation.ai_enabled=false`、
`translation.auto_approval_enabled=false`，尚未接入生产主链。
