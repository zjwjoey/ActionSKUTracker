# Export 模块落地计划

更新日期：2026-08-26

## 1. 实施原则

Export 分两阶段落地：

1. **先交付基础可用版本**：稳定导出当日 ES/ZH 两份无图 Excel；
2. **基础版本验收后再做详细版本**：三表合一、历史 Presence、新 SKU 合并和中文图片。

禁止在基础版本尚未稳定时同时引入历史矩阵、图片嵌入和大文件性能问题。两个阶段复用同一正式来源、字段映射、字典 Join 和 QA 校验，不允许后续再重写一套基础逻辑。

## 2. 阶段 A：基础可导出版本

### A0. 隔离现有代码

当前本地工作区已经存在 ES/ZH 无图导出代码，但尚未形成独立提交。第一步只审查和整理以下范围：

```text
src/action_tracker/exporting/
config/export_profiles.yaml
tests/test_exporting.py
src/action_tracker/cli.py 中的 export 入口
src/action_tracker/config.py 中的导出路径
```

要求：

- 不混入 Dictionary Enrichment、Review Queue、Term Candidates 或字典基线数据更新；
- 不改采集、Presence、Lifecycle、QA 和 Master Writer；
- 不使用 `git add .`；
- 先记录现有行为，再做最小修复。

### A1. 冻结基础 Profile

基础版本只支持两个独立工作簿：

```text
YYYYMMDDAction商品全量_西班牙语版_不带图.xlsx
YYYYMMDDAction商品全量_中文版_不带图.xlsx
```

每个工作簿只包含一张 `商品全量` 工作表，固定 14 列：

```text
图片、编号、标题、分类1、分类2、规格、折后价、原价、单价、
描述、产品详情、图片链接、商品链接、备注
```

基础版本不做：

- 历史上下架明细；
- 三表合一；
- 图片下载、处理或嵌入；
- 全量翻译；
- 导出过程中访问 Action 官网。

### A2. 正式来源解析

基础导出只接受：

```text
QA PASS / PASS_PRESENCE_ONLY
+ FULL_COMMIT
+ 非 dry-run
= 可导出正式来源
```

当日 SKU 集合使用正式 Listing/CURRENT 有效集合，不使用 Sitemap 原始 SKU 数量。找不到唯一正式来源时必须失败，不得自动回退到最近一天或 QA FAIL 数据。

### A3. 西班牙语无图导出

西语表只读取官网事实/Master：

- SKU、标题、分类、规格、价格、单价；
- 西语描述、产品详情、官方标签；
- 图片链接和商品链接；
- 图片列始终空白。

字典不得改写西语事实。发现中文污染、重复 SKU、非 CURRENT SKU、无效价格或商品链接时阻断导出并产生诊断。

### A4. 中文无图导出

中文版必须使用与西语版完全相同的正式 SKU 集合。中文字段按以下优先级解析：

```text
字段级人工覆盖
→ 有效商品字典
→ 正式品牌/类目/术语字典
→ source_hash 有效模型结果
→ 西语 fallback + 精确待审核标记
```

中文、描述或详情缺失不能删除 SKU。价格、图片链接和商品链接必须与西语版逐 SKU 一致。

### A5. Excel 格式

- 第一行筛选并冻结；
- SKU 写为文本；
- 价格写真实数值并使用 EUR 格式；
- 原价仅在有效且严格大于当前售价时显示；
- 链接保持真实 URL 和可点击性；
- 描述和产品详情自动换行；
- 行按 SKU 稳定排序；
- 图片列保留但全部空白。

### A6. Manifest 和只读保护

每个文件生成同名 manifest，至少记录：

- profile ID/version；
- export date、run_id、generated_at；
- source kind 和 source hash；
- SKU 数量和 SKU 集合 hash；
- 中文 fallback 统计；
- 导出校验结果。

导出前后检查 Master、State、Dictionary 和 Snapshot 未被修改。

### A7. 基础版本测试

必须覆盖：

1. 只接受正式来源；
2. QA FAIL、dry-run、未提交 run 被拒绝；
3. SKU 非空且唯一；
4. 非 CURRENT 和 Sitemap-only SKU 被拒绝；
5. 西语事实不被字典覆盖；
6. 中文字段优先级正确；
7. source hash 过期时 fallback；
8. 中文缺失不删除 SKU；
9. ES/ZH SKU 数和集合一致；
10. ES/ZH 价格、图片链接和商品链接一致；
11. 表头、列顺序、筛选、冻结和数值格式正确；
12. 重复导出内容幂等；
13. 来源文件保持只读。

上述测试在 CI 中必须使用临时 fixture 执行；真实正式来源预览只能在本地完成。

### A8. 基础版本试跑与发布

顺序：

1. 小样本单元测试；
2. 使用历史正式 Snapshot 导出预览；
3. 使用最近一次正式 CURRENT 导出 ES/ZH 两份无图文件；
4. 对账 SKU 数、SKU 集合、价格和链接；
5. 人工抽查西语纯净度、中文 fallback 和 Excel 格式；
6. 完整执行 `python -m pytest -q`；
7. 单独提交代码、配置、测试和对应文档；
8. 推送功能分支；
9. 基础版本实际使用至少一次并确认无阻断问题。

CI 绿灯是代码合并前置条件，但不构成正式 Export 发布许可。

## 3. 阶段 A 完成标准

只有同时满足以下条件，基础导出才算可用：

- 命令可以按日期导出 ES/ZH 两份无图文件；
- 来源是唯一正式 run；
- ES/ZH SKU 数、SKU 集合和事实字段完全对账；
- Sitemap-only 没有混入；
- 中文缺失没有导致 SKU 丢失；
- Excel 打开正常，筛选、冻结、价格和链接正确；
- manifest 可追溯；
- 重复运行幂等；
- 来源数据保持只读；
- 完整测试通过；
- 用户确认基础文件可以用于日常工作。

基础版本未达到以上标准时，不开始阶段 B。

## 4. 阶段 B：Template 1 详细版本

阶段 B 只在基础版本稳定后启动。它复用阶段 A 的 Source Resolver、ES/ZH Row Builder、Dictionary Join、格式化和校验。

### B1. 历史 Presence 初始种子

参考 `Action商品上下架明细 26.08.24.xlsx`。由于该表的日期与当前 `config/history_sources.yaml` 不完全一致，建议：

- 把参考表作为已确认历史 Presence 初始种子；
- 既有日期的明确 0/1 原样保留；不完整来源的缺失值写 `UNKNOWN`；
- 不根据 first_seen/last_seen 反推和覆盖旧日期；
- 从新的正式运行日期开始追加日期列；
- 保存种子文件路径、hash、行数和日期列清单。

未来若要从所有历史 Raw 文件重建旧日期，必须先单独完成日期映射和逐期对账，不能在 Template 1 首次实现时同时进行。

### B2. 历史 SKU union

构建一 SKU 一行的长期 union：

- 既有 SKU 保留；
- 当日首次出现的新 SKU 追加；
- 新 SKU 以前日期写 0、当日写 1；
- 既有 SKU 当日不在售写 0；
- 同日期重复导出更新原列，不重复追加；
- 当日为 1 的 SKU 集合必须等于 CURRENT_VALID。

### B3. 三表合一

生成一个工作簿：

1. `商品上下架明细`；
2. `今日西班牙语清单`；
3. `今日中文清单`。

第二、三张表直接复用阶段 A 已验证的 ES/ZH 数据构建器，不能复制另一套字段逻辑。

### B4. 中文图片

- 只在 `今日中文清单` 嵌入；
- 只读取本地已有图片，不启动下载；
- 按 SKU 精确匹配；
- 250×250 像素、白色背景；
- 缺图留空并标记，不删除 SKU；
- 分别进行 50、500、全量 SKU 性能测试。

### B5. 三表交叉 QA

```text
商品上下架明细当日为 1 的 SKU
== 今日西班牙语清单 SKU
== 今日中文清单 SKU
== CURRENT_VALID SKU
```

同时检查 ES/ZH 的顺序、价格和链接一致，记录新品数、历史 union 数、图片成功数和缺图数。

### B6. Template 1 验收

- 三张工作表名称和结构固定；
- 日期列三态语义正确（`1/0/UNKNOWN`；当期完整 CURRENT 列为 `1/0`）；
- 新品正确加入历史 union；
- 同日重导不重复日期列；
- ES/ZH 共用基础版本数据；
- 只有中文表插图；
- 全量工作簿可正常打开和筛选；
- 文件大小、内存和生成时间在本机可接受；
- manifest 和三表对账全部通过；
- 用户确认后再标记 Template 1 为正式可用。

## 5. 建议提交顺序

```text
1. feat: stabilize no-image export source and profiles
2. test: verify ES/ZH export reconciliation
3. docs: mark base export available

基础版本实际验收通过后：

4. feat: add historical presence seed and union
5. feat: compose template 1 workbook
6. feat: embed local images in Chinese sheet
7. test: validate template 1 and full-size performance
8. docs: mark template 1 available
```

## 6. 状态标记规则

文档、配置和代码统一使用以下状态，避免“设计完成”被误写成“功能可用”：

| 状态 | 含义 |
| --- | --- |
| `defined` | 需求和契约已冻结 |
| `implemented_local` | 本地实现，尚未提交或发布 |
| `tested` | 单元和回归测试通过 |
| `preview_verified` | 用真实正式来源生成并人工核对预览 |
| `released` | 代码、配置、测试、文档均已提交，用户确认可用 |

当前状态：基础无图导出和 Template 1 无图三表为 `implemented_local / tested`；中文图片嵌入与独立
`export-history` 仍为后续阶段，不能因为无图导出通过就自动开启。
