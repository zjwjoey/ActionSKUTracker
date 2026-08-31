# Localization Intelligence V1 implementation map

| 责任 | 现有入口 | V1 统一入口 |
| --- | --- | --- |
| 事实读取 | `database.repository.ProductionRepository` | `SourceFacts.from_record` |
| 字典解析 | `dictionary_resolver`, `knowledge.resolver` | `LocalizationEngine.resolve`（兼容层逐步委托） |
| 语义拆解 | 无统一实现 | `localization.semantic.parse_semantic_facts` |
| 品名/规格 | Export dictionary join 的字段拼接 | `localization.planner` |
| 格式 | 分散在导出/规范化代码 | `localization.formatter` |
| 质量门禁 | 粗粒度残留检测 | `localization.validator` |
| AI | `knowledge.ai` 注入式 runner | `localization.ai` provider interface |
| 学习/晋升 | `knowledge.queue`, review queue | `localization.learning` + `promotion` |
| 报告 | 各模块临时 CSV | `localization.service.audit_current` |
| 正式写入 | KnowledgeStore field-level apply | `localization.service.apply_from_audit`（仍受生产开关保护） |

SQLite PRIMARY、Presence/Lifecycle、价格历史、浏览器和图片管线保持冻结，不在本分支重构。
