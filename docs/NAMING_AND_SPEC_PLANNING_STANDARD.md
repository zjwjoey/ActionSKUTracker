# 品名与规格规划规范 V1.0

> 建议 GitHub 路径：`docs/NAMING_AND_SPEC_PLANNING_STANDARD.md`
>
> 本规范定义“哪些事实进入品名、哪些进入规格”，是 Localization Intelligence 的核心 Placement Policy。

## 1. 总原则

品名和规格不是西语字段的一对一翻译。

必须先将官网事实拆为语义事实，再规划字段归属：

```text
商品身份事实 → 品名
消费者选择参数 → 规格
稳定商品组 → 分类
卖点/用途/特点 → 描述
结构化参数/保养/营养/认证 → 产品详情
```

任何重要源事实不得因未进入品名而丢失，应落到规格、描述、详情或 Review Evidence。

## 2. 品名的职责

品名回答：

> “这是什么商品？”

正式品名要求：

- 简短
- 稳定
- 可搜索
- 同类商品核心名称一致
- 普通西班牙语 0 残留
- 不堆砌规格
- 不写价格/促销
- 不写无事实依据的营销形容词

## 3. 标准品名结构

```text
[确认品牌牌] + [必要身份属性] + [核心商品类型] + [必要身份技术Token/型号]
```

“必要身份属性”只包含若删除就会改变消费者对商品类型理解的属性。

## 4. Product Type 优先

商品类型必须优先从：

```text
manual override
product_dictionary
product_type_dictionary
source-hash-valid model candidate
AI UNKNOWN resolution
```

获得。

同类商品应使用统一核心名词。

例如：

```text
paño de microfibra
bayeta de microfibra
```

在同一类目和语义条件下，应尽量归一为：

```text
微纤维清洁布
```

而不是随机出现：

```text
抹布
擦布
清洁巾
微纤维布
纤维清洁布
```

如果这些西语词在业务上确实对应不同商品类型，则通过 Product Type Dictionary 明确拆开。

## 5. 品牌规则

确认品牌：

```text
品牌标准名 + 牌 + 商品通用名
```

例如：

```text
Stanger牌记号笔
Spargo牌微纤维清洁布
```

品牌不确认：

```text
不猜
不加入
BRAND_CANDIDATE
```

人工品名覆盖永远优先，不被自动格式化。

## 6. 系列/IP/角色名

系列/IP/角色名与品牌必须分离。

如：

```text
Capetown
Minecraft
Bluey
Stitch
```

只有有正式证据证明属于系列/IP并且对商品身份有价值时才能保留。

不得因为拉丁词出现在标题/描述里就自动保留。

建议语义类型：

```text
SERIES_TOKEN
IP_TOKEN
CHARACTER_TOKEN
```

## 7. 技术词与规格词必须保留，但由 Planner 决定字段

技术、接口、标准、型号和规格事实不得因中文化被删除。

例如：

```text
LED
USB-C
HDMI
IP44
E27
GU10
A4
ACEA A3/B3
4K
1080p
4000mAh
```

但“保留”不等于全部进入品名。

### 7.1 优先进入品名

当技术词本身定义商品类型时：

```text
LED灯
USB-C数据线
HDMI线
4K摄像头
```

### 7.2 优先进入规格

当技术参数用于选择具体 SKU 时：

```text
E27
10W
220–240V
2m
4000mAh
IP44
```

例如：

```text
西语：Bombilla LED E27 10 W
品名：LED灯泡
规格：E27｜10W
```

若业务需要将 E27 纳入商品身份，可由 Product Type/Placement Rule 明确，不允许模型随机决定。

## 8. 原则上不进入品名的内容

以下优先进入规格：

- 尺寸
- 容量
- 重量
- 数量
- 包装数
- 颜色
- 多款可选
- 尺码范围
- 电压
- 功率
- 长度
- 适配范围

例如：

```text
错误：3件50×60cm多色微纤维清洁布
正确品名：微纤维清洁布
正确规格：3件｜50×60cm｜多种颜色
```

## 9. 功能属性何时进入品名

功能属性进入品名须满足至少一项：

1. 删除后会把商品理解成另一种商品；
2. 是消费者搜索时的核心识别词；
3. Product Type /人工规则明确要求。

例如通常可以：

```text
可充电台灯
无线耳机
双面胶带
防水胶带
```

通常不应：

```text
耐用毛巾
漂亮收纳盒
实用清洁刷
超值胶棒
```

## 10. 材质是否进入品名

材质仅在决定商品类别或搜索身份时进入品名。

例如：

```text
微纤维清洁布
铝合金直尺
不锈钢保温杯
```

普通附加材质进入详情：

```text
材质：塑料
材质：涤纶、锦纶
```

不得把所有 Material 自动塞入品名或规格。

## 11. 规格职责

规格回答：

> “同一商品里，我买的是哪一种/哪一个参数？”

优先容纳：

```text
尺寸
容量
重量
数量
尺码
颜色/款式
适配范围
接口/插头
电压
功率
技术标准
型号/尺寸代码
```

## 12. 规格信息来源

规格规划不能只读取 `spec_es`。

可以从正式源事实中提取：

```text
name_es
spec_es
desc_es
details_es
```

但必须遵守来源事实和数字保护。

如果一个事实只出现在描述/详情里，但明确是选择 SKU 的规格参数，可以提升到 `spec_zh`；同时记录：

```text
spec_source = details_es / desc_es
```

不得凭常识补参数。

## 13. 规格语义槽位

建议标准槽位：

```text
SIZE
DIMENSIONS
DIAMETER
LENGTH
CAPACITY
WEIGHT
QUANTITY
PACK_COUNT
SIZE_CODE
APPAREL_SIZE
COLOR
VARIANT
MATERIAL_IF_VARIANT
VOLTAGE
POWER
CURRENT
FREQUENCY
BATTERY_CAPACITY
SOCKET
INTERFACE
COMPATIBILITY
PROTECTION_RATING
MODEL
```

## 14. 规格顺序

默认顺序：

```text
尺寸/容量/重量
→ 数量/包装数
→ 尺码/适配范围
→ 接口/型号/标准
→ 电气/技术参数
→ 颜色/款式
```

产品类型可以覆盖默认顺序。

例如灯泡：

```text
E27｜10W｜806lm｜220–240V｜暖白光
```

例如清洁布：

```text
3件｜50×60cm｜多种颜色
```

## 15. 规格格式

统一：

```text
乘号：×
范围：–
多项分隔：｜
枚举分隔：、
```

禁止正式规格混用：

```text
x
X
*
|
,
```

作为结构分隔符。

## 16. 标准单位

本项目采用紧凑型零售规格格式：数字与单位不留空格。

统一：

```text
100g
500ml
2kg
50cm
10mm
1.5m
2m²
10W
220V
50Hz
4000mAh
800lm
60°C
```

标准单位不是西语残留。

不得同时出现：

```text
500ml
500 ml
500毫升
```

正式规格只保留一种。

## 17. 小数与范围

```text
15,5 cm（西语源）
→ 15.5cm（规格标准）
```

规格内部使用英文小数点以利机器处理。

范围：

```text
35–42码
220–240V
```

## 18. 数量单位

按商品语义使用：

```text
件
个
双
张
卷
片
支
瓶
盒
包
枚
粒
套
```

无法可靠判断时使用 `件` 作为受控 fallback，并标记低置信度。

## 19. 款式/颜色

统一：

```text
varios colores / diferentes colores → 多种颜色
varias variantes / diferentes variantes → 多款可选
```

不要在同一数据集中随机出现：

```text
不同款式
多种款式
各种款式
多个款式
```

除非语义不同。

## 20. 品名与规格去重

建立 token/semantic-level 去重。

示例：

```text
品名：微纤维清洁布
规格：3件｜多种颜色
```

规格不再写：

```text
微纤维｜3件｜多种颜色
```

但如果“微纤维”是该 SKU 的材质变体而非产品类型，则允许进入规格。

## 21. 同一事实不得静默丢失

Planner 删除品名冗余后必须能解释事实去向：

```text
source semantic item
→ name / spec / description / detail / suppressed_as_duplicate / review
```

`LocalizationPlan` 必须保存 placement evidence，供 Debug 和 Review。

### Localization V1 Final Closure（2026-09-01）

模型只处理 Planner 判定为 UNKNOWN 且来源字段未损坏的字段；输出是候选，不是命名事实。品名、规格和详情的中文结果必须经过字段级 Validator，保留型号、接口、单位和全部数字；人工覆盖拥有内容优先级但不绕过校验。多 SKU 学习规则按 SKU 保存独立 source evidence，不允许用一个 SKU 的 hash 代表聚合候选。

## 22. 具体示例

### SKU 100241

源：

```text
Paño de microfibra para el suelo Spargo
50x60 cm | varios colores
```

若 Spargo 已正式确认品牌：

```text
品名：Spargo牌微纤维地板清洁布
规格：50×60cm｜多种颜色
```

如果 Spargo 未确认：

```text
品名：微纤维地板清洁布
规格：50×60cm｜多种颜色
Review：BRAND_CANDIDATE
```

### SKU 102235

```text
Paños de microfibra Spargo
3 unidades | varios colores
```

标准：

```text
品名：Spargo牌微纤维清洁布（品牌确认后）
规格：3件｜多种颜色
```

### SKU 40258

```text
Alfombrilla para cortar
A3
```

标准：

```text
品名：切割垫
规格：A3
```

A3 是标准尺寸代码，保留原样。

### SKU 38067

```text
Barra de cola
50 ml | diferentes variantes
```

标准：

```text
品名：胶棒
规格：50ml｜多款可选
```

## 23. Planner 验收

必须用测试覆盖：

- 品名普通西语 0 残留
- 品名不保留西语原名
- 品牌必须有证据
- 技术 Token 不被误翻译
- 技术参数不丢失
- 尺寸/数量优先进入规格
- 品名/规格去重
- 同类 Product Type 稳定
- `x` → `×`
- `|` → `｜`
- `50 ml` → `50ml`
- `100 gramos` → `100g`
- `3 unidades` → `3件`
- `varios colores` → `多种颜色`
- `diferentes variantes` → `多款可选`
- 数字事实保持

知识命中顺序固定为 Manual Override → Product Dictionary → Confirmed Brand/Category → Product Type/Phrase/Term/Tech Token → 同源模型缓存 → 确定性规则 → 本地 AI UNKNOWN 候选。AI 只提供候选，不直接写正式字段；品名、规格规划必须保留技术 Token、数量和单位事实。
# Localization field contract

The planning standard is consumed through the shared seven-field mapping:
`name_es→name→name_zh`, `cat1_es→cat1→cat1_zh`, `cat2_es→cat2→cat2_zh`,
`spec_es→spec→spec_zh`, `unit_price_es→unit_price→unit_price_zh`,
`desc_es→description→desc_zh`, and `details_es→details→details_zh`.
Technical tokens are identity facts and cannot be translated away.
