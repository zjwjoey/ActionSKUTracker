# Action 西班牙站导出模块架构

## 1. 模块定位

Export 是正式交付层，不是采集器、翻译器、生命周期判断器或新的 Master。

它只读取已经通过正式门禁的数据，将以下三类数据组合成固定 Excel 模板：

1. 历史 SKU Presence 事实；
2. 当日正式有效在售 SKU 及官网西班牙语事实；
3. 本地字典产生的中文标准化结果及本地图片。

Export 绝对不得：

- 重新访问 Action 官网；
- 用 Sitemap 数量直接代替当日有效在售 SKU 数量；
- 修改 Master、Snapshot、Lifecycle State、Dictionary 或历史源文件；
- 因中文、详情或图片缺失而删除在售 SKU；
- 在导出过程中调用翻译服务、下载图片或推断上下架状态。

## 2. 数据流

```text
正式运行（QA PASS + FULL_COMMIT）
        │
        ├─ 当日有效 CURRENT / Listing Presence ──→ 当日在售 SKU 集合
        │
        ├─ Master / Formal Snapshot ─────────────→ 西语事实、价格、链接、标签
        │
        ├─ Product Dictionary / Review 结果 ─────→ 中文名称、分类、规格、描述
        │
        ├─ 历史只读批次 ─────────────────────────→ 各日期 Presence 0/1
        │
        └─ 本地图片缓存 ─────────────────────────→ 中文清单图片
                                      │
                                      ▼
                             Export 模板与校验
                                      │
                                      ▼
                          Excel 工作簿 + Manifest
```

## 3. 字段所有权

| 数据 | 唯一权威来源 | Export 的权限 |
| --- | --- | --- |
| 当日有效 SKU 集合 | 正式 QA PASS 的 Listing/CURRENT Presence | 只读、校验、排序 |
| Sitemap SKU | Sitemap 证据 | 仅用于覆盖检查，不直接写成当日在售 |
| 西语名称、分类、规格、描述、详情 | Master / 正式 Snapshot | 原样读取，不允许字典改写 |
| 价格、图片链接、商品链接、官网标签 | Master / 正式 Snapshot | 原样读取和格式化 |
| 中文名称、分类、规格、描述、详情 | 本地字典与字段级人工覆盖 | 按固定优先级 Join |
| 历史日期 Presence | 对应日期的只读历史批次 | 按 SKU 集合写 0/1 |
| 本地商品图片 | 已存在的图片缓存 | 只允许读取和嵌入 |
| Excel 列顺序、工作表名称、显示格式 | Export Profile | 负责冻结和校验 |

## 4. 模板注册

导出模块采用版本化模板，不允许静默改变已经发布的列结构。

| 模板 | 状态 | 输出 |
| --- | --- | --- |
| Template 1 无图 | 已提交到当前功能分支并通过回归测试，待用户验收 | 一个 Excel、三张工作表 |
| 基础 ES/ZH 无图 Profile | 已提交到当前功能分支并通过回归测试，待真实日常验收 | 两个独立 Excel |
| 独立历史 Presence Export | 已完成设计、尚未实现 | 与 Template 1 第一张表复用同一 Presence 构建服务 |

Template 1 的完整字段契约见 `docs/EXPORT_PROFILE.md`。

## 4.1 实施顺序

基础阶段已完成两个独立无图文件，并新增 Template 1 无图组合预览：

- 西班牙语全量无图；
- 中文全量无图。

基础版本包含正式来源解析、14 列结构、ES/ZH 对账、manifest 和只读保护。Template 1
当前已完成历史 union、三表合一和 0/1 Presence；中文图片嵌入仍是后续独立阶段。

完整顺序和验收条件见 `docs/EXPORT_IMPLEMENTATION_PLAN.md`。

## 5. Template 1 的工作簿结构

固定输出一个工作簿，包含且只包含以下三张表：

1. `商品上下架明细`
2. `今日西班牙语清单`
3. `今日中文清单`

三张表共享同一个 `export_date`、`run_id` 和正式 SKU 来源。

- 第一张表是历史 SKU union，一 SKU 一行，总行数通常大于当日在售数量；
- 第二、三张表只包含当日有效在售 SKU；
- 第二、三张表的 SKU 数、SKU 集合、顺序、价格和链接必须完全一致；
- 只有第三张表嵌入本地图片。

## 6. 模块内部建议

```text
src/action_tracker/exporting/
├── profiles.py          # 模板版本、工作表与字段契约
├── source_resolver.py   # 解析 QA PASS + FULL_COMMIT 正式来源
├── history.py           # 历史 SKU union 与 Presence 0/1
├── dictionary_join.py   # 中文字段级 Join
├── image_resolver.py    # 只查找本地图片，不负责下载
├── validation.py        # 三张表的交叉校验
├── excel_writer.py      # Excel 集中写入、格式与图片嵌入
└── service.py           # 导出编排与 manifest
```

历史 Presence 不得在 Template 1 和独立 `export-history` 中分别实现两套逻辑；两种输出必须复用同一个只读构建服务。

## 7. 正式导出门禁

正式导出前必须同时满足：

1. 来源 run 为 `QA PASS + FULL_COMMIT`；
2. 当日有效 SKU 非空、唯一，且与正式 CURRENT 集合一致；
3. 当日数量以有效 Listing/CURRENT 为准，不以 Sitemap 原始数量为准；
4. 第一张表当日日期列中 `1` 的合计等于当日有效 SKU 数；
5. 第二、三张表 SKU 集合完全相同；
6. Sitemap-only、无效 SKU、重复 SKU 不得混入当日清单；
7. 中文、详情或图片缺失只能标记，不能删 SKU；
8. 导出前后 Master、State、Dictionary 和历史源文件哈希不变。

例如：2026-08-26 正式有效 SKU 为 5,476 时，第一张表 `26.08.26` 列必须恰好有 5,476 个 `1`，第二和第三张表必须各有 5,476 个唯一 SKU。Sitemap 即使额外包含其他 URL/SKU，也不能改变这三个结果。

## 8. 输出证据

每个工作簿必须生成同名旁路 manifest，至少记录：

- `template_id`、`template_version`；
- `export_date`、`run_id`、`generated_at`；
- 正式来源类型和来源哈希；
- 历史 union SKU 数；
- 当日有效 SKU 数；
- 当日新增 SKU 数；
- 第一张表当日 `1` 的数量；
- ES/ZH 工作表行数与 SKU 集合哈希；
- 中文图片成功数、缺失数；
- 中文待审核字段统计；
- 三张表的校验结果。

## 9. 当前实现状态

截至 2026-08-27：

- ES/ZH 两个独立无图文件已经可以从正式来源导出；
- Template 1 无图三表、第一张表当日列和新 SKU union 已实现；
- 中文表图片嵌入尚未实现，继续独立冻结；
- Profile、基础导出和 Template 1 无图三表已实现并通过回归，正式来源仍必须来自 QA/FULL_COMMIT；
- 图片下载仍是独立任务，Export 只消费已经存在的本地图片。

## 10. CI 与导出验证边界

导出回归测试在 CI 中使用临时 Master/Snapshot fixture，验证字段、集合、manifest、幂等和只读保护。CI 不读取本机正式 runtime，不访问 Action 官网，不下载或嵌入真实图片，也不生成正式交付文件。

真实来源解析、QA 通过后的导出预览和人工视觉检查仍属于本地发布流程；CI 通过不能替代这些步骤。
