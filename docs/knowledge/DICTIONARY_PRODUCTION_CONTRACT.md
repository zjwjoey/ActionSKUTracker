# Dictionary Production V1 合同

知识源（Git 版本化 CSV/YAML）与生产结果分离：

- 知识源保存人工确认的商品、品牌、类目、术语和字段覆盖；
- `product_localizations` 保存一个 SKU 当前正式生效的中文结果；
- 每个字段独立保存来源、状态和 source hash；
- 优先级：人工覆盖 > 商品字典 > scoped dictionary > 品牌/类目/术语 > 有效模型缓存 > 西语 fallback。

Apply 必须在预览中记录 `source_hash、base_commit_id、dictionary_hash`，提交前重新校验。
不一致即 `STALE_TRANSLATION_PREVIEW`，整批回滚；禁止部分 SKU 写入。当前正式 Apply
仍由 `knowledge.production_apply_enabled=false` 和既有 dictionary gate 双重关闭。
