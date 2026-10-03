# Naming History Migration Inventory — 2026-10-03

来源分支：`origin/fix/naming-history-export-20260917@0b3795b7565c9432ee0419e2dfb8937fad56bdb0`  
基线：`origin/main@b47e4c56a0274fdddceff347e2571d77c1476014`

本文件是迁移盘点，不是合并请求。旧分支包含大量历史脚本、数据资产和旧中文品牌展示规则，不能整体合入当前主线。

## 分类

| category | 内容 | 处理 |
|---|---|---|
| ALREADY_IN_MAIN | SQLite PRIMARY、CommitBundle/QA/integrity 边界、Translation V1 safety defaults、当前 export/read-only contracts | 以 main 为准，不重复迁移 |
| NEEDS_SEMANTIC_MIGRATION | 详情键值解析、源事实绑定、数字/单位/技术 token 保留、字段级 provenance 和 review contract | 只按当前模块边界逐项移植，补测试后再提独立变更 |
| DATA_ASSET_REVIEW_REQUIRED | `data/dictionary/*`、Stage5/Stage6 gold/review 数据、历史修复 CSV、训练集和报告 | 只读审计、hash/来源/owner review；不批量复制到生产 |
| OBSOLETE | 旧的 Excel-only 主链说明、旧 fallback 直接写中文、旧品牌“品牌+牌”展示示例、重复的历史脚本 | 不迁移；以当前 NO_BRAND 和 formal path 契约为准 |
| DO_NOT_MIGRATE | 真实 runtime、生产 SQLite、`F:\按日期整理`、历史 Master、Cookie/profile、密钥、图片和旧分支工作树 | 永不进入 Git 或闭环分支 |

## 已审查重点

- 命名、规格和详情逻辑只迁移确定性事实保护，不迁移旧的品牌展示政策。
- 任何迁移必须保留 `source_hash`、SKU 主键、字段来源、审计状态和可回滚证据。
- Stage6 只允许闭环任务列出的两个 provenance 提交；Scrapling 只允许 v3 实验提交。
- 本 inventory 不删除、不移动 recovery tag，不替代 owner 对数据资产的逐项批准。

结论：`MIGRATION_ONLY`。
