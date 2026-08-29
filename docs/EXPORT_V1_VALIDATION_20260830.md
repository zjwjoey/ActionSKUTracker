# Export Foundation V1 验收记录

更新日期：2026-08-30

## 正式来源

- 日期：2026-08-29；
- run：`2026-08-29_184646`；
- QA：PASS；
- 提交：FULL_COMMIT；
- 来源：`MASTER_CURRENT`；
- SKU：5,396。

## 已生成文件

输出目录：`F:\ActionSKUTracker\runtime\exports`

- `20260829Action商品全量_西班牙语版_不带图.xlsx` + manifest；
- `20260829Action商品全量_中文版_不带图.xlsx` + manifest；
- `20260829Action商品全量_西班牙语版_带图.xlsx` + manifest；
- `20260829Action商品全量_中文版_带图.xlsx` + manifest。

四份工作簿均通过：14 列固定表头、唯一 SKU、5,396 行数据、冻结首行、自动筛选、价格数值、
商品链接合法和四份导出的事实列一致性检查。带图导出缺图时保留所有 SKU。

## 历史上下架导出

独立 `export-history` 已使用 `1 / 0 / UNKNOWN` 三态和“历史来源审计”工作表，避免把不完整来源
误写成下架 0。

## 回归结果

当前完整本地测试：**261 passed**。

## 仍需人工/运行门禁

- 用户确认模板字段与业务使用方式；
- 真实图片切片及全量同步；
- Template 1 带图版本已实现，但仍需真实图片资产后进行切片与全量验收；
- 若启用 SQLite，完成 Shadow parity 后再切换 Primary；
- 不将 runtime 导出文件、图片、报告或密钥加入 Git。
