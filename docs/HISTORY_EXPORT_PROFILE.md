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
把历史表冒充实时 CURRENT。Template 1 的第一张表复用同一 Presence 构建服务，并可在
自己的三表模板中增加展示字段，但不能改变 Presence 事实。

当前交付 Profile 冻结的日期列为：2026-01-08、2026-06-28、2026-07-05、
2026-07-12、2026-07-19、2026-07-26、2026-08-02、2026-08-10、2026-08-17、
2026-08-24、2026-08-31、2026-09-07、2026-09-14。2026-04-05 及其他未列入
Profile 的中间批次仍可作为原始研究来源，但不会出现在这张交付表中。Profile 最新日期
之后新注册的批次会自动追加到最右侧；因此下一次周一只需登记新的只读来源，不会丢失旧列。

## 滚动留档与幂等

每次成功导出都会把经过同一验证的历史矩阵留档到
`runtime/exports/history_archive/YYYYMMDD_Action商品上下架明细.xlsx`，并写入同名
manifest。下一次导出先读取已登记来源，再读取留档中尚未出现的日期，按 SKU 合并；
历史 0/1/UNKNOWN 事实不会由当前 Master 或生命周期推断。重复导出同一日期时以 SKU +
日期矩阵的语义哈希判断：相同矩阵返回 `UNCHANGED`，不同矩阵返回
`HISTORY_ARCHIVE_DATE_CONFLICT`，禁止静默覆盖。

历史 Presence 的集合、去重和三态规则可在 CI 用小型临时来源验证；真实导出命令为：

```powershell
python -m action_tracker export-history --date YYYY-MM-DD
```

命令只读 `config/history_sources.yaml` 中的来源，不访问官网，不修改 Master、State 或
Dictionary，并在同目录生成对应 `.manifest.json`。
