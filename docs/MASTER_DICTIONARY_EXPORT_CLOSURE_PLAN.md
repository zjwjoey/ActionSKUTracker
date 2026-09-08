# Master、字典与正式导出数据收口计划 V1

> 状态：本轮收口实现已落地，数据库迁移与正式 Apply 仍须在下一次受控 run 中验收。
>
> 目标：让一次确认过的西语清洗与中文翻译，成为下一次导出的正式来源；不再靠每期
> Excel 事后修补来维持质量。

本轮已加入：

- `localization_fields` 字段级 provenance 投影；
- `product_fact_versions` 的 Raw/Normalized 事实留存；
- `localization_patches` 与 append-only patch event 生命周期；
- SQLite CURRENT 导出的硬 Release Gate；
- 对应的迁移、不可变性和 Gate 回归测试。

这些结构采用 additive migration。生产库会在下一次 `ProductionWriter` 初始化时自动创建，
不会删除或覆盖既有事实；正式中文导出在字段审批完成前会被 Gate 阻断，而不是静默发布。

## 1. 要解决的问题

当前 SQLite 已是运行主库，但“可研究的最终字段”仍分散在三处：

- SQLite/Master 保存官网事实与部分本地化字段；
- `product_dictionary.csv` 保存 SKU 名称、分类和规格等字典字段；
- 每期最终 Excel 又包含人工或定向修复后的结果。

三者没有一条受控的回写链路。因此导出会再次取得旧值、fallback 值或网页残留，随后
又在 Excel 中重复修复。新品数量与需修复字段数量没有可比性：修复针对的是全部在售
历史 SKU 的数据债，不只是当日新增 SKU。

## 2. 已知基线（2026-09-08）

以下数字只用作迁移验收基线，不代表任何日期的永久 SKU 数：

| 项目 | 事实 |
| --- | --- |
| 2026-09-07 成品 ES/ZH 导出 | 5,552 SKU |
| 2026-09-08 SQLite `CURRENT` | 5,547 SKU |
| 当前 SKU 的中文 `cat2` 空值 | 324 |
| 当前 SKU 的中文 `description` 空值 | 344 |
| 当前 SKU 的中文 `details` 空值 | 343 |
| 商品字典记录 | 8,680 |
| 字典 `UNREVIEWED` | 7,908 |
| 字典 `NEEDS_REVIEW` | 648 |

**日期必须对齐。** 2026-09-07 的 5,552 条导出与 2026-09-08 的 5,547 条 CURRENT
不能直接逐行视为错误。所有迁移、对账和导出均须绑定同一 `business_date`、`run_id` 与
来源哈希。

## 3. 目标架构

```text
正式采集快照 / 官网事实
          │
          ▼
SQLite fact snapshot ──► 已审核 field-level localization ──► 正式 Export
          ▲                         ▲
          │                         │
  西语机械清洗补丁包           Dictionary Resolver / 人工审核
                                    ▲
                              术语、品牌、分类、模型候选
```

核心原则：

1. **SQLite 是唯一正式数据源。** Excel 只是一种不可反向污染数据源的投影。
2. **字典是规则、候选和审核层，不是第二个正式事实库。** 字典解析出的值必须经过
   字段级 Apply 写入 SQLite 本地化记录，正式 Export 不再临时把多处来源拼接在一起。
3. **西语事实与中文派生值永不互相覆盖。** 西语标题、分类、规格、描述、详情、价格和
   URL 只由官网事实/经过证据约束的清洗层拥有；中文字段只由本地化层拥有。
4. **任何修复均是字段级、可追溯、可撤回的补丁。** 一条 SKU 的中文品名修复不会冻结
   该 SKU 的分类、规格、描述或详情。
5. **正式研究版不得静默 fallback。** 预览版允许西语 fallback，但必须带字段级状态；
   正式研究版的中文字段须全部为已批准值，或属于事先声明、可审计的例外。

## 4. 正式字段契约

### 4.1 官网事实层：Raw Fact 与 Normalized Fact

官网原始证据与可导出的规范化事实必须在逻辑上分层：

```text
raw_fact        = 原始响应/快照所见值，连同证据路径、抓取时间与响应 hash 保留
normalized_fact = 对 raw_fact 的可解释、可重放的机械规范化投影
```

例如：

```text
raw_fact:        Material:: Plástico&nbsp;
normalized_fact: Material: Plástico
```

`raw_fact` 不能被 HTML 清理、冒号修复、空白折叠或 UI 文案移除永久覆盖。`normalized_fact`
只能引用某一版本 `raw_fact` 的 hash，并记录使用的规范化规则版本。实现初期可以使用同一
张 SQLite 表和不同版本/字段命名，而不强制立刻拆成两张物理表；但读取语义、证据保留与
审计记录必须等同于双层模型。

| 导出字段 | 唯一所有者 | 允许的处理 |
| --- | --- | --- |
| SKU、商品链接、图片链接 | 官网事实 | URL/SKU 完整性验证 |
| 西语品名、分类1、分类2、规格 | 官网详情页/主面包屑 | 格式清洗，不按常识改分类 |
| 西语描述、产品详情 | 官网详情页 | 去 HTML、UI 残留、机械分隔格式；保留原意与重复字段 |
| 折后价、原价、单价 | 官网价格事实 | 原价仅在 `original_price > sale_price` 时展示 |
| 在售/新品/重新出现 | 生命周期、同日 Presence | 不由详情或字典推断 |

西语补丁必须同时记录：`sku`、`field`、raw hash、normalized 前后值、规范化规则版本、
证据类型、操作者、时间与 `run_id`。raw hash 不匹配时不得自动应用到新事实，只进入复核
队列。

### 4.2 中文本地化层（SQLite Localization）

| 导出字段 | 正式值字段 | 候选来源 | 发布要求 |
| --- | --- | --- | --- |
| 中文品名 | `name` | 商品字典、术语、人工覆盖、批准模型候选 | 商品本体准确；不自动附加品牌 |
| 中文分类1/2 | `cat1` / `cat2` | 分类字典 + 已核实西语主面包屑 | 分类1为固定 15 类；分类2有依据 |
| 中文规格 | `spec` | 术语字典、单位规则、人工覆盖 | 数量、单位、型号、颜色等事实一致 |
| 中文描述 | `description` | 经过审核的翻译 | 无西语残留、网页残留或臆造内容 |
| 中文产品详情 | `details` | 经过审核的字段级翻译 | 完整保留字段和值，不丢失或合并事实 |

字段 provenance 是**字段级而非 SKU 级**。每个中文字段均须独立保存：

```text
<field>.value
<field>.source
<field>.review_status
<field>.source_hash
<field>.updated_at
<field>.applied_commit_id
```

其中 `<field>` 分别为 `name`、`cat1`、`cat2`、`spec`、`description`、`details`。可以
保留 SKU 级汇总状态用于列表筛选，但它只能由字段状态聚合得出，绝不能替代字段状态。
因此“品名已批准”不表示“描述、分类或详情已批准”。现有本地化表已有部分来源字段；实施
时必须补齐缺失的字段级状态和审计投影。

### 4.3 品牌规则（以当前业务决定为准）

- 中文品名**不主动加入品牌名或“牌”字**；
- 品牌、IP、系列、型号只有在它是识别商品所必需的型号/系列事实时才可保留；
- `A3`、`D3`、`USB-C`、`20D`、接口型号、剂量等属于商品事实，不能因去品牌规则丢失；
- “行李牌”“扑克牌”等商品本体中的“牌”绝不能误删。

`EXPORT_PROFILE.md`、`DICTIONARY_ARCHITECTURE.md` 中仍存在“品牌标准名＋牌＋商品”
的旧规则；在启用任何正式 Apply 前必须统一更新，避免 Resolver 和人工规范互相覆盖。

## 5. 实施阶段

### 阶段 0：冻结与只读盘点

**目的：** 防止在不明来源的情况下继续把最终 Excel 覆盖回生产数据。

- 记录当前 Git HEAD、SQLite commit、当前 `run_id`、字典基线 hash、配置开关；
- 对 2026-09-07 的 ES/ZH 成品和对应正式 run 生成不可变 manifest；
- 列出“同 SKU 同字段”在 SQLite、字典、成品 Excel 三处的差异，区分：展示格式差异、
  已确认修复、未确认冲突、跨日期差异；
- 暂停把临时 Excel 直接作为 Master 替换源。

**验收：** 能对任一差异回答“它属于哪个日期、哪个字段、哪个来源、是否可回写”。

### 阶段 1：字段契约与导出路径收口

**目的：** 删除正式 Export 中“直接拼接字典 + Master + fallback”的多头读路径。

- 将 `export_profiles.yaml` 改为只读取同一 SQLite 快照的事实字段与已应用本地化字段；
- Dictionary Resolver 输出仅作为 Apply 候选，不能直接成为正式导出字段；
- 明确两种导出等级：
  - `preview`：允许字段级 `FALLBACK_ES`，并输出未完成清单；
  - `research_release`：不允许未声明的 fallback、`PENDING`、`UNREVIEWED` 字段；
- 更新过期的品牌展示规则、字段名映射与中文 15 类目配置；
- 增加字段来源列到 manifest，不增加用户模板中的临时 Excel 列。

**验收：** 给定一个 `run_id`，每一列都可明确追溯到 SQLite 的一个字段及其来源状态；
同一输入重复导出数据内容 hash 一致。

### 阶段 2：建立受控回写（Patch / Apply）

**目的：** 让已确认修复永久生效且不误伤新数据。

建立两类只增不改、可审计的补丁包：

1. `official_fact_patch`：西语 HTML/UI 清洗、详情分隔符、主面包屑分类、原价展示语义；
2. `localization_patch`：中文品名、分类、规格、描述、详情的确认值。

Patch 是不可变 revision，不允许 `UPDATE patch` 修改历史记录。每次修复均创建新 patch；
状态转换也必须是追加事件：

```text
PATCH_CREATED → PATCH_APPROVED → PATCH_APPLIED
                         └──────→ PATCH_REVOKED
```

撤销已应用 patch 时，创建新的 `PATCH_REVOKED` 事件和对应的反向 revision；不得删除、改写
原 patch 或篡改当时的前后值。Patch、审批、Apply、撤销事件均须带操作者、时间、原因、
关联 revision、`run_id` 与 hash。

Apply 规则：

- 一条补丁只改一个 SKU 的一个字段；
- 只有 `source_hash` 与基准西语事实一致时才可自动应用；
- 不一致、源数据内部冲突（如标题“胶囊”而详情“片剂”）和有争议译文进入 Review Queue；
- 每次正式 Apply 生成 DB commit、前后值、行数、失败项、回滚包和 hash manifest；
- 绝不从带图片 Excel、手工排序、单元格格式或跨日期数据推断业务事实。

**验收：** 对已应用字段重跑 Dictionary Resolver 和 Export 时，结果不再回退；撤销某一
补丁可只影响目标字段。

### 阶段 3：迁移 2026-09-07 已确认成果

**目的：** 把已有修复从“成品 Excel”转成正式数据资产。

迁移顺序：

1. 以 2026-09-07 的正式西语无图表为基线，抽取机械清洗与经官网核实的分类补丁；
2. 将中文终审表中已确认的字段拆成 `localization_patch`，而不是整行覆盖；
3. 对每条补丁检查对应 2026-09-07 snapshot 的西语哈希；
4. 对当日仍有效、且西语哈希未变化的 SKU，将批准值前移到当前本地化记录；
5. 对已下架、哈希变化或源冲突 SKU，仅存入历史 snapshot / review，不污染当前 head；
6. 重新导出同日 ES/ZH 无图表并逐字段对账。

**验收：** 2026-09-07 的再生 ES/ZH 表与已确认成品的业务字段一致；所有差异必须在
manifest 中明确列为允许的格式差异或已知例外。

### 阶段 4：中文覆盖率清零与审核分流

**目的：** 不再把 5,000 多条“已有翻译”视作同一质量等级。

- 将当前 8,680 条字典按字段拆分为：已批准、可自动应用、待审核、源冲突、无官网文本；
- 优先清理当前在售 SKU 的中文分类2、描述、详情空值；历史 SKU 进入单独 backlog；
- 模型输出只能进入候选层，必须携带输入字段 hash、模型版本和置信/规则原因；
- 人工确认直接形成字段级覆盖，不再通过修改最终 Excel 隐式确认；
- 对正式研究版设置零未翻译中文字段目标；无法翻译的字段必须有 `SOURCE_CONFLICT` 或
  `SOURCE_MISSING` 的明确例外，不可伪装为已完成。

**验收：** 当前正式 SKU 的中文版能够从 SQLite 一次生成，且没有未标记西语 fallback。

### 阶段 5：发布门禁与日常增量

**目的：** 防止未来重复形成数据债。

正式导出前强制检查：

1. 导出日期、`run_id`、SQLite commit 和 manifest 完全一致；
2. ES/ZH SKU 集合、价格、图片 URL、商品 URL 完全一致；
3. SKU 非空、唯一；必填字段符合 Profile；
4. `original_price` 仅在严格大于当前售价时导出；
5. 西语无 HTML、`null`、`undefined`、双冒号和 UI 残留；
6. 中文正式版无未批准 fallback、无明显西语残留、无字段级 source hash 失配；
7. Export 与 SQLite 投影逐字段差异为 0；唯一允许例外必须写入 manifest；
8. 导出只能读，绝不修改 Master、SQLite 或字典。

发布 Gate 的机器可读结果固定为：

```ini
SKU_SET_MISMATCH = 0
FACT_MISMATCH = 0
UNDECLARED_DISPLAY_MISMATCH = 0

UNAPPROVED_ZH = 0
STALE_ZH = 0
SPANISH_RESIDUAL = 0
SOURCE_HASH_MISMATCH = 0
```

任何非零值均为发布阻断，不得仅以 warning 继续发布。唯一例外是逐条、显式登记的
`EXPLICIT_EXCEPTION`，且必须包含 SKU、字段、原因、责任人、创建/到期时间、证据和
批准记录；例外仍须出现在 manifest 与 QA 报告中。导出器不得自行创建例外。

日常流程固定为：`正式 run` → `增量字典候选` → `审核/Apply` → `正式 Export`。
只处理 NEW、官网字段 hash 变化、待审核和源冲突 SKU；未变化老 SKU 不重新翻译。

**验收：** 新品仅产生增量候选；同一 SKU 的已批准中文字段在次日导出保持稳定，除非官网
事实 hash 变化或人工发布新补丁。

### 阶段 6：大批量新品提取与补全队列

**目的：** 处理一次出现数百条乃至更多新品的情形，不让详情抓取限流、Cloudflare 访问
中断或翻译积压阻塞当日 Presence 提交，也不把未补全数据伪装为完整数据。

#### 6.1 两阶段处理模型

```text
Listing / Sitemap / 类目覆盖成功
        │
        ├── 冻结当日 Presence、SKU、链接、价格、Listing 已有事实
        │       └── 正式提交当日在售清单（前提：Listing QA PASS）
        │
        └── 为每个需补详情 SKU 建立持久 Enrichment Queue
                ├── Detail 获取
                ├── 图片获取与 250px 白底处理
                ├── 中文候选生成
                ├── 字段审核 / Apply
                └── 仅补字段，不反推在售或下架
```

“新品”必须先以 `NEW_PRESENCE_CONFIRMED` 被记录，详情任务仅决定其字段丰富度，不能
因为排队尚未完成而漏记新品、重记新品或阻断已经完整的 Listing 结果。

#### 6.2 持久队列与状态

每一条需补全任务至少保存：`sku`、`run_id`、`business_date`、任务类型、官网 URL、
优先级、`source_hash`、尝试次数、下次尝试时间、最后结果、详情完整状态与证据路径。

其中 `CATEGORY_MISSING` 是独立的详情队列任务类型：Listing 卡片只提供一级分类，二级
分类必须来自商品详情页主面包屑。当前记录若二级分类为空，应进入该队列并按详情页事实
补齐；不能从商品标题、交叉陈列页或常识推断。`CATEGORY_MISSING` 与 `MISSING_FIELD`
同级优先处理，仍受 `BOTH` Presence 证据和每轮详情配额约束。

建议的详情状态为：

| 状态 | 含义 | 可否写入正式 Presence |
| --- | --- | --- |
| `PENDING` | 尚未开始或等待配额 | 可以 |
| `IN_PROGRESS` | 当前 worker 正在处理 | 可以 |
| `COMPLETE` | 详情字段与证据已完成 | 可以 |
| `INCOMPLETE` | 正常失败，等待后续补全 | 可以，字段必须标记未完成 |
| `ACCESS_INTERRUPTED` | 403 / 429 / Cloudflare 等访问限制中断 | 可以，禁止伪造或重试轰炸 |
| `SOURCE_MISSING` | 官网确实没有某独立字段 | 可以，保留可审计空值 |
| `REVIEW_REQUIRED` | 源冲突或语义不确定 | 可以，禁止自动臆造中文值 |

任务必须以 `sku + task_type + source_hash` 幂等去重。官网内容未变时，重跑不得新建无限
重复任务；官网详情 hash 变化时才生成新的补全版本。

#### 6.3 大批量策略

- **先保完整 Listing，后补详情。** 当天只要 Listing 覆盖、解析和 QA 完整，数百个新品
  仍可进入当日 0/1 Presence；
- **批次化而非硬截断。** 不以“本轮只抓 N 条详情”让其余新品消失，而是按确定性队列
  分批执行并留下可恢复 backlog；
- **优先级透明。** 可按新品、图片缺失、中文关键字段缺失、历史遗留、人工指定 SKU 排序，
  但所有未处理项都必须可见；
- **限流服从 AccessController。** 429、403 或挑战页立即暂停新详情任务并按 cooldown
  回队，不绕过验证、不换代理、不伪造浏览器指纹；
- **断点续跑。** 进程退出、网络失败或重启后从持久队列恢复，不重新扫描并重复请求已完成 SKU；
- **不混淆日期。** 补到的是创建任务时 `run_id` 的详情证据；若产品已更新，则新 hash
  另建版本，不能把旧详情静默写回新日期。

#### 6.4 交付与翻译规则

- 当日 ES/ZH `preview` 可包含 `PENDING` / `INCOMPLETE` 新品，但备注/manifest 必须给出
  详情、图片、中文字段的实际状态；
- `research_release` 若要求详情完整，则只在 backlog 归零或所有未完成项属于已声明
  `SOURCE_MISSING` / `ACCESS_INTERRUPTED` 例外时发布；
- 翻译任务消费 **详情已完成或 Listing 文本足够且 source hash 稳定** 的 SKU；不能将
  半截详情送进字典后再让模型补造缺失事实；
- 大批量新品结束后，应形成一个有数量、SKU、状态和失败原因的 `backlog manifest`，而
  不是仅在终端显示“还有若干待提取”。

#### 6.5 必须先做的只读审计

实施前先确认当前提取模块的实际限制，而不凭猜测改动：

1. 每轮详情计划数量、完成数量、跳过数量和队列是否持久化；
2. `NEW` SKU 在 Listing 成功、Detail 中断时是否仍进入正式提交；
3. 详情限额、并发、cooldown、重试上限和恢复入口；
4. 图片、翻译和字典任务是否错误地以“详情本轮已成功”为唯一入口；
5. 大量新品时 CSV/SQLite 事务、导出和排队是否仍保持 SKU 幂等。

#### 6.6 验收场景

新增自动化回归和一次隔离演练，至少覆盖：

| 场景 | 必须结果 |
| --- | --- |
| 当日 1 个新品 | 与原有日常流程一致 |
| 当日 400 个新品，详情全部成功 | 400 条均入 Presence，队列全部 `COMPLETE` |
| 当日 400 个新品，详情第 2 条触发挑战 | Listing 仍可提交；剩余任务为可恢复 backlog，不丢 SKU、不误下架 |
| 中途重启 | 已完成详情不重复请求；未完成从 checkpoint 恢复 |
| 同 SKU 次日官网详情变化 | 新 hash 新版本；旧证据可追溯，不覆盖新事实 |
| 访问限制持续存在 | 不无限重试；队列记录 cooldown/失败原因；正式 Presence 不被详情误伤 |

**验收：** 大批量新品不会导致 SKU 遗失、重复翻译、重复详情请求或将不完整详情写成
完整数据；任一时刻均可导出 backlog 的准确数量、SKU 集合、状态和恢复依据。

## 6. 不在本计划内的事项

- 不重写 Listing、Sitemap、生命周期、缺失/下架判断；
- 不通过自动化方式绕过 Cloudflare 或验证码；
- 不用模型反向“补造”缺失官网西语事实；
- 不把图片二进制写进 SQLite；
- 不将所有历史 8,680 条一次性强制重翻。先收口当前正式 SKU，再做历史 backlog。

## 7. 最终发布标准

只有达到以下条件，才能称为“Master / 字典 / 导出闭环完成”：

- 同日期、同 run 的 SQLite、ES Export、ZH Export SKU 集合和官网事实字段零差异；
- 已确认修复可从 SQLite 再生，不依赖手工修过的 Excel；
- 中文字段来源、审批状态、源哈希和 Apply commit 可追溯；
- 正式研究版无静默 fallback，例外均有明确状态；
- 字典规则、人工覆盖、正式本地化值职责不重叠；
- 任一新增 SKU 最多只进入一次增量翻译与审核链路，而不触发旧 SKU 全量返工。
