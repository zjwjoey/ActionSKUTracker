# Action 商品中文字段标准化规范 V1.0

> 建议 GitHub 路径：`docs/CHINESE_LOCALIZATION_STANDARD.md`
>
> 本规范是品名、分类、规格、单价、描述、产品详情的唯一中文输出标准。
>
> 品名/规格的字段归属细则见 `NAMING_AND_SPEC_PLANNING_STANDARD.md`；模块实现与学习机制见 `LOCALIZATION_INTELLIGENCE_ARCHITECTURE.md`。

## 1. 规范目标

同一份西语正式事实，在相同：

```text
policy_version
knowledge_version
source_hash
```

条件下，应产生确定性一致的中文字段。

目标不是逐字翻译，而是：

```text
事实不变
语义准确
字段归属正确
中文自然
格式统一
可审计
可学习
```

## 2. 适用字段

```text
中文品名
分类1
分类2
规格
单价
描述
产品详情
```

正式中文字段不得反向修改：

```text
SKU
价格
URL
西语事实
Presence
Lifecycle
```

## 3. 基于 2026-08-31 参考工作簿的现状

参考文件：`20260831Action商品全量_三表版带图.xlsx`。

该文件仅用于规则设计和 acceptance reference，不是 SQLite PRIMARY 的替代真源。

观察到：

```text
历史上下架 union：8,679 个商品数据行（加表头共 8,680 行）
今日 CURRENT：5,379 个 SKU（加表头共 5,380 行）
分类1：15 个固定中文值
分类2：108 个中文值，无空白
规格非空：5,378 个 SKU
```

规格存在多种并存格式，例如：

```text
|
｜
x
×
50 ml
50ml
```

因此本规范要求 Formatter 确定性收口。

## 4. 普通西班牙语零容忍

正式中文字段中不允许普通西班牙语词汇或句子残留。

例如不得出现：

```text
colores
varios
diferentes
incluye
unidades
piezas
plástico
madera
recargable
compatible
transparente
```

必须中文化。

## 5. 允许保留的非中文 Token

以下不属于“西语残留”：

### 5.1 已确认品牌

```text
3M
Adidas
Stanger
Spargo
```

仅品牌字典或人工证据确认后允许。

### 5.2 已确认系列/IP/角色名

例如 `Capetown` 只有确认其系列身份并且字段语义需要时允许保留。

### 5.3 技术/接口/标准/认证

```text
LED
RGB
USB-C
HDMI
Bluetooth
Wi-Fi
IP44
IP65
E27
GU10
ABS
PVC
PP
PE
PET
FSC
GRS
Qi2
ACEA A3/B3
4K
1080p
```

### 5.4 型号/尺寸标准代码

```text
A3
A4
H7
CR2032
XH-120
```

### 5.5 标准单位

```text
ml
g
kg
mm
cm
m
m²
L
W
V
A
Hz
mAh
lm
°C
```

## 6. 西语残留 Validator

必须做 token-level 分析。

逻辑：

```text
发现拉丁/带重音 Token
↓
确认品牌？ → 合法
确认系列/IP？ → 合法
TECH_TOKEN？ → 合法
MODEL_TOKEN？ → 合法
STANDARD_UNIT？ → 合法
标准认证/代码？ → 合法
否则 → SPANISH_RESIDUAL
```

禁止使用旧式：

```text
字段里只要包含中文，就忽略全部拉丁文本
```

## 7. 中文品名

完整规则见 `NAMING_AND_SPEC_PLANNING_STANDARD.md`。

最低要求：

- 普通商品名完全中文化
- 西语原品名不保留
- 确认品牌按统一格式
- 技术/型号事实保留但正确规划
- 不把尺寸、数量、颜色、价格机械塞进品名
- 不出现营销夸张词
- 人工锁定值优先

## 8. 分类1

永久冻结为：

1. DIY五金
2. 办公文具
3. 宠物用品
4. 厨房餐具
5. 服饰鞋包
6. 个人美容
7. 家居布置
8. 家务清洁
9. 旅行用品
10. 食品饮料
11. 数码影音
12. 玩具
13. 兴趣手作
14. 园艺户外
15. 运动用品

不得自动创建第16类。

未知映射：

```text
CATEGORY_REVIEW
```

## 9. 分类2

分类2是受控字典，不是自由翻译。

正式键：

```text
(cat1_es, cat2_es)
```

一个正式键只能有一个当前中文标准值。

例如：

```text
Oficina y papelería / Accesorios de oficina
→ 办公文具 / 办公配件

Hogar / Artículos de limpieza
→ 家务清洁 / 清洁用品

Hobby / Manualidades
→ 兴趣手作 / 手工制作
```

未知值先 Candidate，再审核/晋升。

## 10. 规格

详细规则见 `NAMING_AND_SPEC_PLANNING_STANDARD.md`。

格式统一：

```text
100g
50ml
3件
50×60cm
220–240V
10W
4000mAh
E27｜10W｜多种颜色
```

结构符号：

```text
× 乘号
– 范围
｜ 多规格分隔
、 枚举
```

## 11. 单价

单价只能基于官网正式 unit price，不由 AI 推算。

只允许单位本地化，不改变数字。

示例：

```text
5,80 €/kg → 5,80 €/千克
5,00 €/l → 5,00 €/升
0,99 €/ud. → 0,99 €/件
```

未知单位：

```text
UNIT_REVIEW
```

## 12. 描述的职责

描述是自然语言的商品功能摘要。

它负责：

- 核心用途
- 功能特点
- 有意义的材质/性能
- 使用场景
- 官网明确卖点

不负责机械堆参数。

结构化技术/护理/营养信息优先进入产品详情。

## 13. 描述完整中文化

普通西语必须全部中文化。

允许品牌/系列/技术 Token 按证据保留。

例如参考表中：

```text
Capetown 彩色系列适合各种浴室……
```

只有 Capetown 确认为 SERIES_TOKEN 才能保留；否则进入 Review，不能因为模型“不知道怎么翻”就直接放过。

## 14. 描述语言风格

要求：

```text
客观
简洁
自然中文
事实型
避免广告文案化
```

删除/改写无信息量营销句：

```text
你一定会喜欢
不容错过
完美之选
超级好用
快来体验
```

不得增加官网不存在的效果承诺。

## 15. 描述标点

中文正文统一：

```text
，。；：（）
```

句号后不留无意义空格：

```text
错误：第一句。 第二句。
正确：第一句。第二句。
```

保留必要技术符号：

```text
USB-C
FSC®
IP44
```

## 16. 描述空值

源描述为空时：

```text
中文描述保持空 / 待审核
```

AI 不得仅凭商品名称和常识凭空创作一段描述。

## 17. 产品详情职责

产品详情是结构化参数区。

正式格式：

```text
字段：值；字段：值；字段：值
```

例如：

```text
颜色：蓝色；材质：锦纶、涤纶；洗涤说明：最高60°C机洗；商品编号：100241
```

## 18. 产品详情 Key 标准化

字段名必须来自 `detail_key_dictionary.csv` 或 Pending Candidate。

高频标准：

| 西语 Key | 中文 Key |
| --- | --- |
| Color | 颜色 |
| Material | 材质 |
| Cantidad | 数量 |
| Contenido | 含量 |
| Potencia | 功率 |
| Voltaje | 电压 |
| Longitud del cable | 线缆长度 |
| Instrucciones de lavado | 洗涤说明 |
| Instrucciones de secado | 干燥说明 |
| Instrucciones de planchado | 熨烫说明 |
| Uso previsto | 用途 |
| Apto para el lavavajillas | 可用洗碗机清洗 |
| Apto para el microondas | 可用于微波炉 |
| Pilas incluidas | 含电池 |
| Recargable | 可充电 |
| Número del artículo | 商品编号 |

未知 Key：

```text
DETAIL_KEY_REVIEW
```

不得每次让模型自由创造不同中文 Key。

## 19. 产品详情值

普通西语值必须中文化；技术/型号/单位按白名单保留。

布尔：

```text
Sí → 是
No → 否
```

多值：

```text
Poliéster, Poliamida
→ 涤纶、锦纶
```

## 20. 产品详情商品编号

```text
详情中的商品编号 == 当前 SKU
```

否则：

```text
DETAIL_SKU_MISMATCH
→ BLOCK
```

## 21. 产品详情推荐排序

```text
颜色/款式
→ 材质
→ 尺寸/容量/数量
→ 核心技术参数
→ 电气参数
→ 兼容/用途
→ 洗涤/清洁/保养
→ 安全/年龄/限制
→ 食品营养
→ 认证/可持续
→ 商品编号
```

商品编号最后。

## 22. 描述与详情去重

结构参数已进入详情时，不要求描述机械重复。

例如：

```text
详情：材质：棉；颜色：蓝色
```

描述无需自动生成：

```text
颜色为蓝色，材质为棉。
```

除非材质是正文核心卖点。

## 23. 数字事实保护

所有中文字段中的数字事实必须可追溯到源事实。

AI 不得：

```text
220V → 240V
3件 → 4件
50ml → 60ml
```

允许的纯格式变化：

```text
50 x 60 cm → 50×60cm
15,5 cm → 15.5cm
```

Validator 必须区分“格式变化”和“事实变化”。

## 24. 字段来源与状态

每个中文字段建议提供：

```text
value
source
status
source_hash
freshness
policy_version
```

字段来源可能为：

```text
manual_override
product_dictionary
product_type_dictionary
brand_dictionary
category_dictionary
term_dictionary
phrase_dictionary
tech_token_dictionary
model_cache
ai_candidate
```

## 25. 正式状态

至少支持：

```text
READY
STALE
REVIEW_REQUIRED
SOURCE_BLOCKED
MISSING
LOCKED
```

已有项目状态可兼容映射，但不得把 STALE 伪装为 CURRENT/READY。

## 26. 当前表中的规范化示例

### SKU 10280

源：

```text
Gomas elásticas Office Essentials
100 gramos
5,80 €/kg
```

建议：

```text
品名：橡皮筋
分类1：办公文具
分类2：办公配件
规格：100g
单价：5,80 €/千克
描述：多用途。经济装橡皮筋，可快速捆扎物品或整理文件。
产品详情：颜色：米色；材质：橡胶；商品编号：10280
```

### SKU 38067

```text
品名：胶棒
分类1：玩具
分类2：手工制作
规格：50ml｜多款可选
单价：5,00 €/升
产品详情：含溶剂：否；含量：50ml；胶水类型：液体胶；商品编号：38067
```

### SKU 40258

```text
品名：切割垫
规格：A3
描述：三层结构，自修复表面切割后不易留下明显刀痕。也可用于手工制作，垫面带刻度和辅助线，便于精准切割。
产品详情：颜色：绿色；商品编号：40258
```

### SKU 100241

若 Spargo 品牌确认：

```text
品名：Spargo牌微纤维地板清洁布
规格：50×60cm｜多种颜色
单价：0,99 €/件
描述：可吸附污垢和油脂。含52%再生聚酯纤维。适用于各种地面，可清洁光滑地面且不易留下痕迹。
产品详情：颜色：蓝色；材质：锦纶、涤纶；洗涤说明：最高60°C机洗；熨烫说明：不可熨烫；干燥说明：可低温烘干；清洁介质类型：清洁布；用途：通用；商品编号：100241
```

### SKU 102235

```text
品名：Spargo牌微纤维清洁布（品牌确认后）
规格：3件｜多种颜色
单价：0,33 €/件
描述：多用途，加厚耐用，每包含多种颜色。采用高品质纤维制成，吸水能力强。
产品详情：颜色：彩色；材质：涤纶、锦纶；吸水性：是；数量：3件；洗涤说明：最高60°C机洗；熨烫说明：不可熨烫；干燥说明：不可烘干；商品编号：102235
```

## 27. Review Reason Codes

统一：

```text
NAME_REVIEW
PRODUCT_TYPE_REVIEW
BRAND_CANDIDATE
SERIES_REVIEW
CATEGORY_REVIEW
SPEC_FORMAT_REVIEW
UNIT_REVIEW
DESCRIPTION_REVIEW
DETAIL_KEY_REVIEW
DETAIL_VALUE_REVIEW
TECH_TOKEN_REVIEW
SPANISH_RESIDUAL
SOURCE_HASH_CHANGED
NUMERIC_FACT_MISMATCH
DETAIL_SKU_MISMATCH
```

## 28. 正式中文 PASS Gate

一个 SKU 达到 `CHINESE_STANDARD_PASS` 至少满足：

```text
品名非空且无普通西语
分类1属于固定15类
分类2命中正式映射
规格通过 Formatter/Validator
单价数值未改变
描述无普通西语或未审拉丁词
详情 Key 全部标准化
详情商品编号匹配 SKU
所有数字事实保持
人工 LOCK 未被覆盖
source_hash/freshness 正确
```

Review 项不得被伪装 PASS。

## Knowledge status contract

知识可信状态仅使用 `PENDING`、`AI_CANDIDATE`、`SEED_REVIEWED`、`HUMAN_REVIEWED`、`LOCKED`、`REJECTED`。其中 `SEED_REVIEWED` 表示仓库固定种子，不能冒充人工审核；Review Queue 的 `APPROVED/REJECTED/RESOLVED` 是任务状态，不能与知识状态混用。Manual Override 优先于商品字典，模型缓存必须同源且有效，Source Damage 字段进入 `SOURCE_BLOCKED`，不得交给 AI。

### 2026-09-01 V1 收口补充

人工覆盖写入后必须再次经过最终 Validator；模型缓存只在可信质量状态且匹配当前 PRIMARY 四字段 source hash 时可用。Source Damage 按字段隔离：损坏字段禁止进入 AI，其他未知字段仍可按 `requested_fields` 进入 AI。任何 AI 结果都必须保持 JSON 合同、数字和技术 Token，不得因模型输出失败而降低校验规则。
# Contract enforcement note (2026-09-01)

The naming and localization rules are applied through the canonical field
contract, not by string-suffix inference.  Technical identifiers (for example
USB-C, LED, E27, IP44, A3 and AAA) remain in their original form unless an
explicit approved dictionary normalization exists.  Manual corrections are
validated again and clear resolved field issues while preserving structural
source warnings.
