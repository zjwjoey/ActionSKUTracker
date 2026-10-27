# 历史 SKU 汇总导出（STEP 7）

> 当前状态：历史 Presence 构建服务、独立 `export-history` CLI 和单表工作簿已实现；
> 当前已用真实历史来源生成并通过结构校验，后续仅需按需要做业务验收。

历史汇总是 Export，不是 Master。它从 `config/history_sources.yaml` 中列出的每个
只读历史批次建立 Presence 矩阵；一个 SKU 一行，每个历史日期一列。Presence 只有三态：
`1` 表示有可靠存在证据，`0` 表示来源完整且明确证明不存在，`UNKNOWN` 表示无法可靠判断。

Template 1 会把同一套历史 Presence 构建结果作为工作簿第一张表；独立
`export-history` 只能复用该构建服务，不得另写一套 Presence 判断逻辑。

## 不可突破的规则

- Presence 只能来自对应日期的原始批次；不得由 `first_seen`、`last_seen`、当前
  Master 或字典推断。
- 每个来源和 Seed 必须声明 `presence_capability`、`absence_capability`、
  `observation_complete`、`evidence_level`；缺失或非法配置必须阻断导出。
- 源文件 `F:\按日期整理` 永远只读。
- 同一批次内 SKU 重复时，Presence 只表示“至少有一行”，但原始行数、唯一 SKU 数
  和重复行数必须写入 manifest；不从重复行猜选商品字段。
- 商品字典只补充稳定展示字段（中文名、品牌、中文类目及最新西语标准字段），不影响
  日期 Presence。

## 输出列

独立 `export-history` 的固定列为：`序号、编号、中文品名、图片链接、商品链接`；随后
按实际来源日期排列 Presence 列。Presence 写 `1/0/UNKNOWN`，不追加当前状态判断，也不
把历史表冒充实时 CURRENT。Template 1 的第一张表使用同样5个固定列，
复用同一 Presence 构建服务，不能改变 Presence 事实。

独立模板和Template 1版本均为2，统一上述5列契约；Template 1的另两张清单不变。
Seed 和指定 `presence_header` 的来源均接受 `0/1/UNKNOWN`，显式 UNKNOWN 不因
来源声明完整而改写为0。指定的日期列不存在、值非法或同SKU同日冲突时阻断。
矩阵来源中只有0或UNKNOWN且不属于已有Seed的行，不能单独证明该SKU曾在售。

本次导出可在临时 `history_sources.yaml` 中声明 `excluded_months: ['2026-04']`。
该范围设置同时排除Seed日期列和独立来源，排除日期独有的存在证据不能增加SKU；
其他日期仍有存在证据的同SKU必须保留。未配置时不改变既有来源范围。
Manifest记录排除范围、固定列、各来源Hash和成品Hash。生产配置无需为单次导出修改。
新增到历史union的当前SKU也逐日期使用历史来源能力：仅完整且支持缺席证明的来源
允许将没有记录的SKU判为0；不完整或能力未知时仍为UNKNOWN，显式UNKNOWN保持不变。

当前配置来源实际日期包括：2026-01-08、2026-01-09、2026-04-05、2026-06-28、
2026-07-01、2026-07-05、2026-07-06、2026-07-12、2026-07-13、2026-07-19、
2026-07-20、2026-07-26、2026-07-27、2026-08-02、2026-08-10、2026-08-17、
2026-08-24。

历史 Presence 的集合、去重和三态规则可在 CI 用小型临时来源验证；真实导出命令为：

```powershell
python -m action_tracker export-history --date YYYY-MM-DD
```

命令只读 `config/history_sources.yaml` 中的来源，不访问官网，不修改 Master、State 或
Dictionary，并在同目录生成对应 `.manifest.json`。
