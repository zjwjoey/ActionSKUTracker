# Action SKU Tracker QA 规则

## 1. QA 结果

| 状态 | 含义 | 是否允许正式提交 |
| --- | --- | --- |
| PASS | Presence 和必要质量检查完整 | 是，且必须非 dry-run |
| PASS_PRESENCE_ONLY | 有效 Sitemap Presence 已冻结，后续 Listing/Detail 不完整 | 只允许提交 Presence/Lifecycle；字段来源必须标记 |
| FAIL | Presence 或关键数据不可信 | 否 |

QA FAIL 和 dry-run 都可以保存 Snapshot、Staging 和报告，但不得更新 Master、known_skus 或 offline_skus。

## 2. Collection 规则

### QA-COL-001：Sitemap 有效性

Sitemap 响应、解析和数量必须健康。无效 Sitemap 不能作为 Presence 或 Listing 降级依据。

### QA-COL-002：主 Listing 覆盖

15 个主类目必须记录计划页、完成页、错误和限制状态。无有效 Sitemap fallback 时，主 Listing 不完整导致 FAIL。

### QA-COL-003：补充入口

Nuevo 和 Promoción semanal 是补充 Presence/标签证据，不替代全部主类目覆盖。

### QA-COL-004：访问限制

401、403、429、挑战页或 BLOCKED 必须显式记录。拒绝页不得当商品页解析。

## 3. Presence 规则

### QA-PRES-001：不完整观测

观测不完整时相关 SKU 为 UNKNOWN；不能推进 missing_count、OFFLINE 或批量缺失。

### QA-PRES-002：冻结顺序

Presence 必须在 Detail 之前冻结。Detail 结果不能增删已冻结的当日 CURRENT 集合。

### QA-PRES-003：Sitemap-only

Sitemap-only SKU 可以进入证据和 Review，但不能未经有效商品确认直接写入当日清单。

### QA-PRES-004：数量阈值

SKU 总量、新品、缺失和 Sitemap/Listing gap 按 `config/settings.yaml` 阈值检查。数量只用于异常检测，不是固定目标。

## 4. Lifecycle 规则

### QA-LIFE-001：FIRST_SEEN

历史从未出现且今天有效出现的 SKU 才是 NEW/FIRST_SEEN。

### QA-LIFE-002：REAPPEARED

历史出现过、上一有效状态为 MISSING/OFFLINE、今天有效重新出现，才产生 REAPPEARED。FIRST_SEEN 同日不得 REAPPEARED。

### QA-LIFE-003：缺失推进

只有有效缺失观测才能把 MISSING_FIRST 推进为 MISSING_CONTINUED，并在达到 `offline_confirmation_runs` 后转 OFFLINE。

### QA-LIFE-004：同日幂等

同一业务日期重复运行不得重复增加 missing_count、重复事件或重复首次出现。

## 5. Detail 规则

### QA-DETAIL-001：非权威性

Detail 只补字段。详情失败、空白或访问中断不表示下架。

### QA-DETAIL-002：字段来源

每个 Snapshot 行必须区分 Listing、Detail、Baseline/Pending 来源。带入字段不得伪装为当天新抓取。

### QA-DETAIL-003：访问中断

Presence 已完整冻结后，Detail 中断记为 DETAIL_ACCESS_INTERRUPTED/ACCESS_INTERRUPTED，可停止详情队列，但不否定 CURRENT。

### QA-DETAIL-004：补充应用

detail-retry 结果只有在父 observation 正式有效、SKU 一致、详情 QA 通过时才能通过 detail-apply/backfill 写回。

### QA-FACT-001：字段内容合法性

字段非空不等于字段有效。对本轮 Listing 权威事实，以及状态为 `COMPLETE` 的 Detail
事实，QA 必须拒绝已知网页/UI 控件文本、HTML 残留、`null`/`undefined` 等占位符、将
`Nuevo`/`Promoción semanal` 写入正式类目、以及产品详情的 `::`、`: ;`、`;;`、Tab/换行
等结构污染。该规则只识别明确污染，不根据商品语义改写官网西语事实。

官网未提供独立规格或描述时允许为空，仍由字段完整性/来源状态单独表达；延迟或访问
中断的 Detail 不因历史补充字段而否定已冻结的 Presence。

## 6. Master 与价格规则

### QA-MASTER-001：CURRENT 语义

CURRENT 只包含真正当前有效商品，不混入历史 OFFLINE 商品。

### QA-MASTER-002：集中写入

Master 只能通过集中 Writer 原子更新，失败时保留原文件和诊断。

### QA-PRICE-001：类型和范围

价格必须为有效数值，并通过配置的最小/最大范围检查。

### QA-PRICE-002：原价

原价只有在有效且严格大于当前售价时显示；当前售价/原价不能单独推断促销。

### QA-PRICE-003：已知无效批次

已确认的历史字段错位和无效价格批次必须由明确规则排除，不能作为模型或导出的可信价格。

## 7. Dictionary 规则

### QA-DICT-001：主键与 schema

商品 SKU、品牌关系、类目关系、术语键和人工覆盖键必须唯一；schema 不匹配直接失败。

### QA-DICT-002：字段级优先级

人工字段覆盖 > 有效商品字典 > 正式品牌/类目/术语 > source hash 有效模型结果 > 审核队列。
正式中文导出不得用西语 fallback 冒充完成翻译；官方 source 为空时目标字段必须为空并标记 `NO_SOURCE`。

### QA-DICT-003：source hash

西语源字段变化时，旧模型结果失效并进入审核；不得静默沿用。

### QA-DICT-004：源事实损坏

SOURCE_DAMAGED/SOURCE_POLLUTED 不得通过中文回译伪造西语事实。

### QA-DICT-005：增量范围

日常只处理 NEW、source hash changed、NEEDS_REVIEW。未变化老 SKU 不产生新翻译，也不修改 updated_at。

### QA-DICT-006：术语晋升

TERM_CANDIDATE 必须经人工 APPROVED 才能进入正式术语字典。

### QA-DICT-007：Review 去重

同一稳定 review_id 不得每天重复入队；解决后转 RESOLVED，拒绝后保留 REJECTED 审计。

### QA-DICT-008：西语残留

中文品名和中文规格中的普通西语残留不得标记为 AUTO_READY；品牌、型号和技术缩写可
按品牌字典/人工确认保留原文。描述和详情不得以西语 fallback 充当中文完成；未完成字段进入审核队列。

### QA-DICT-009：Apply Gate

Dictionary Apply 默认只允许 dry-run。预览必须逐字段记录旧值、新值、来源、Resolver 状态和原因；
`field_diff.csv` 只允许六类中文派生字段，旧值等于新值不得计为实际变化。未来 `--commit` 必须同时满足
QA/FULL_COMMIT、未过期的 Audit、Resolver 全部 AUTO_READY、CURRENT SKU 集合一致、Master hash 未并发变化，
并逐文件验证选中字典和基线 manifest 的 SHA-256 一致。`production_enabled` 和正式品牌策略只能是 YAML
布尔值；字符串 `"false"` 等配置必须拒绝。默认正式 Apply 不接受 PROVISIONAL/UNKNOWN 品牌。写入还必须
通过唯一备份、锁、暂存、不可变事实校验、原子替换及替换后回读；后续校验失败必须恢复备份并把状态写入
manifest。`dictionary_apply.production_enabled=false` 时必须拒绝。
未 AUTO_READY 的 SKU 只能进入 review_required.csv，不能部分写入 Master。

Apply 还必须执行字段级审批保护：六个中文派生字段逐字段判断，只有白名单来源
（人工覆盖、已确认商品/类目/术语字典或经字段审批的既有中文值）且状态为
`READY` 的字段才可写入；若字段携带显式 `approval_status`，仅
`APPROVED/HUMAN_APPROVED/AUTO_APPROVED/CONFIRMED/LOCKED/HUMAN_REVIEWED`
可通过。`PENDING`、`REJECTED` 等状态只跳过该字段，不得因同一 SKU 其它字段
可用而被连带写入。source hash 匹配且 `quality_status=OK` 的模型缓存仍不是 Owner 决策，
Resolver 必须将其保持为待审候选，不能作为 `READY` 来源；`review_required.csv` 和 coverage 报告应保留名称/规格候选值，供人工审核。

### QA-DICT-010：详情术语规则

产品详情术语规则必须绑定该条西语详情的属性键、属性值和必要商品语境。只有西语与中文详情
条目数一致、顺序可对齐，且对应数字/技术 token 一致时，才允许规则修正已知错译；条目不匹配
或语境不确定时保留候选原值并进入审核。详情顺序、重复键、原有分隔符及未命中片段必须保留，
只替换被规则确认的键/值片段；规则版本和逐项修正写入 Stage 5 候选/审核记录。该规则只生成候选，
不直接写正式字典或生产本地化。
详情词典必须包含版本号及类型正确的键映射、上下文规则、值规则和数字后缀规则；Stage 5 生成、工作簿审计和正式发布门禁共用同一结构校验器，配置损坏时失败关闭，且配置哈希参与候选身份。若整格每个属性键和值均有唯一适用的版本化规则，Stage 5 可以按源顺序和原分隔符确定性生成，不调用模型；只要任一条目、值或商品语境未覆盖，就不得把规则片段与模型猜译拼成整格，必须整体回退到既有模型/审核路由。
对已有批准映射的源枚举值，目标译值必须命中显式认可候选（含标准译值）；不在词典白名单中的译值保持不动并产生 `DETAIL_VALUE_TRANSLATION_UNRECOGNIZED`，进入人工审核，不静默通过或自动猜译。
配置中的 `closed_enum_keys` 进一步声明必须由规则覆盖的封闭枚举字段；其源值若未命中任一版本化值规则，会产生 `DETAIL_SOURCE_VALUE_UNMAPPED_REVIEW`。Stage 5 在创建模型请求前将其转入人工队列，不消耗模型调用且不允许自动闭合；正式 Export Gate 阻止严格发布，全量工作簿审计将该 finding 归入 L4。此类信号只要求补词典/人工审核，不自动修改目标值。未声明为封闭枚举的自由值字段不适用此硬约束。
Stage 5 每个模型请求还必须携带同字段保护事实账本：数字、单位、技术 token、识别到的认证，以及源文本中实际命中的已批准材质/属性/服饰/规格/数量/单位术语。该账本仅用于提示模型且保存在请求证据中；同一源词若存在多个不同的已批准译法，必须显式标记歧义并列出候选，不得由模型自行择一。请求 ID 必须绑定实际选中的字典内容哈希，防止词典/账本变化后重放旧模型输出。最终接受仍以 reject-only Guard 和人工复核 finding 为准。词典覆盖之外的事实不会因账本缺项而被视为可省略。

### QA-DICT-011：认证与官网源异常

FSC、PEFC、BCI/Better Cotton、Fairtrade、GOTS、Rainforest Alliance 等已识别认证事实，
源字段出现时中文同字段必须保留等价认证身份；源字段没有时不得新增。`source_consistency_flags`
识别出的可疑官网属性只作为源异常审核标记，西语原值保持不变，不由翻译规则推断或静默过滤。
认证标记识别不得依赖 Unicode `\b` 将拉丁商标与中文分开（例如 `采用FSC®认证`、`Fairtrade体系`）；
`公平贸易` 单独出现属于通用语义，不足以认定 Fairtrade 认证，只有源字段明确含 Fairtrade 时才可在
同字段比较中把“公平贸易”作为其中文译法。不得把一般 `Comercio justo` 反推为认证。
源字段跨字段数字/单位冲突比较会先归一化单复数和确定的单位等值（质量 kg/g/mg、体积 L/cl/mL、
长度 m/cm/mm/inch），仅用于判断源字段是否表达相同物理量，不换写或转换官网字段。明确的多包装规格
（如 `6 × 33,3 g`）按包装数、单件量和整包量比对，允许原文显示精度可解释的舍入差；计数单位仍区分
张、对与一般件数。规格与详情的对比仅采用明确且语义相符的标签（如 `Cantidad` 对件数、`Contenido/Peso`
对质量、裸商品尺寸标签对规格）；排除营养成分、包装尺寸、晾晒总长等不同属性，不以未标注数字推断冲突。
自由描述仅比较明确标注的同属性数值。原始值与比较依据都保留在证据中，规则必须有等值、冲突及语境反例测试。
Daily QA 将检测到的源异常/跨字段冲突写入本次 `qa_report.findings`，保留 SKU、规则码、异常字段/键值、
六个官网西语字段快照、来源哈希及 `REVIEW_ONLY_SOURCE_UNCHANGED` 动作。这是审核证据，不改变 Presence、
生命周期或源值。
Snapshot 同时生成独立 `SOURCE_ANOMALY.csv`，按每个异常 evidence（含重复条目的独立序号）输出稳定 `qa_id`、SKU、异常码、原字段/键值、
六字段西语快照、source hash、规则 manifest、状态和处理动作；`SOURCE_ANOMALY.manifest.json` 记录 CSV 行数、CSV SHA-256 及关联 `qa_report.json` SHA-256，`write_snapshot()` 写完后会调用 `verify_source_anomaly_manifest()` 校验 schema、唯一 ID、行数与两个文件的哈希，校验失败时 Snapshot 报错；没有异常时保留空数据行但写出表头，避免把“缺少文件”
误读成“已确认没有异常”。该文件与业务 `备注`、一般 QA 检查分离，且不改变本轮 QA 结果。
源异常详情键/值规则集中在 `config/stage5/source_anomaly_rules.json`，候选批次与 Stage 6 策略清单
记录该规则版本、配置文件 SHA-256 和检测实现 SHA-256；任何规则或检测逻辑变化都会改变策略身份。异常配置缺版本、规则列表无效、标识重复或正则无法编译时失败关闭。

### QA-DICT-012：自由文本模型候选审核边界

Stage 5 对标题、规格、描述、详情等字段调用模型得到的值始终是候选。即使数字、单位、技术 token、认证、否定、术语和结构 Guard 全部通过，也必须保持 `review_status=PENDING` 并写入 Owner Review；Guard PASS 只表示未触发已实现的确定性拦截，不代表语义完整或可自动发布。只有具有当前 source hash 的显式 Owner 决定、审核人和审核时间，且最终审核值再次通过 Guard，才可进入 Stage 6 预览；Apply 仍受独立授权和生产门禁约束。不得因压缩启发式或术语覆盖检查未命中而跳过人工审核。已批准且 source-bound 的确定性字典/规则解析仍按既有字段所有权合同处理。

### QA-EXP-012：备注与翻译 QA 分离

中文导出的业务 `备注` 只展示在售/新品/促销/可持续状态、折扣与官网原始标签身份，不混入“中文字段待审核”等翻译处理状态。标准中文导出与 Template 1 均生成独立 `QA_LOG.csv` sidecar，按 SKU/字段记录发布门禁 finding，另记录 fallback 汇总；重复 finding 不去重，通过稳定 occurrence 序号保留且确保 `qa_id` 唯一，manifest 绑定 sidecar 的文件名、行数和 SHA-256。工作簿、QA_LOG 和 manifest 作为一组发布，失败时恢复旧版本，防止新表配旧审计记录。`QA_LOG.csv` 不增加工作簿列、不改 Profile 版本，也不写回 Master/SQLite。官网源异常仍由 Snapshot 的 `SOURCE_ANOMALY.csv` 独立留痕。

全量中文候选复查使用只读入口 `scripts/audit_chinese_localization.py`。它按 SKU 对齐西语/中文
工作簿，输出 hard-guard 命中、详情规则预览、源异常和同源多译法；长度压缩与术语差异只标记复核，
不直接认定翻译错误。规范化后重复表头或一个逻辑字段匹配多个列别名时立即失败，不继续生成可能错列的审计结果。报告还会统计结构化详情源键/源键值对的现有词典覆盖率，并列出未覆盖项及已观察到的译法；即使规则覆盖完整，若同一西语键值仍有多个中文译法，也会列出各译法出现数、SKU 样例和规则覆盖状态（仅复核，不自动判错）。
同时输出完整 `DETAIL_RULE_REVIEW.csv`：包含全部未覆盖键/值及所有有译法分歧的键/值，不受 JSON 摘要中前 200/300 项显示上限影响。每条有稳定 review ID、源键/值、覆盖 occurrence、SKU 样例、全部观察到的目标候选及候选是否获批标记；目标候选只来自现有工作簿观察，永远写为未批准，不应用到规则或工作簿。配套 manifest 绑定 CSV 行数/hash、源/目标工作簿 hash 和详情词典 hash，审计 JSON 再绑定 CSV 与 manifest hash。该队列用于把“按 SKU 补丁”转成“按源字段规则全表收敛”；覆盖缺口是规则发现线索，不等价于翻译错误或自动改写许可。入口不写回输入工作簿。

详情规则候选随后按 `candidate_id` 审核，而不是按单个 SKU 补丁审核。候选身份必须包含来源键/值、审核原因、源/目标/词典 hash 和商品语境范围；缺失语境的旧队列只能标记为 `<unknown>`，不能作为全局规则。Owner 的 `APPROVE` 必须绑定 `data/qa/localization_regressions_v1.jsonl` 中能证明同一源键/值和正确目标的回归 case；空白或不完整决策表返回 `NOT_STARTED/FAIL`，不得报告为 accepted。批准提案只生成候选文档，随后必须展开为逐 SKU、逐详情位置的 source-bound repair manifest，由既有 `build_preview` 再校验 source hash、target hash、详情顺序、重复键和政策 hash；该流程不写工作簿、Master、字典或数据库。
分类复核另输出 `CATEGORY_RULE_REVIEW.csv`：按源一级类目、源二级类目和候选目标一级类目作用域聚合 `CATEGORY_MAPPING_UNAVAILABLE/AMBIGUOUS/MISMATCH`，包含出现数、SKU 样例、所有观察到的中文候选及已批准映射（如有）。工作簿中的观测译法仅是待审候选，`candidate_is_approved=false`；不得自动晋升类别字典或改写工作簿。manifest 绑定 CSV、源/目标工作簿和当前类别字典 hash，审计 JSON 再绑定 CSV 与 manifest hash。逐 SKU 原始 finding 仍完整保留在 `QA_LOG.csv`；聚合队列不能把频次或共识当成人工批准。
已批准术语审核另输出 `TERM_RULE_REVIEW.csv`：将 `APPROVED_TERM_CANONICAL_ABSENT_REVIEW`、`APPROVED_TERM_CROSS_FIELD_REVIEW`、`APPROVED_TERM_UNSUPPORTED_TARGET_REVIEW` 和 `TERM_FORBIDDEN_TRANSLATION` 按源词、术语类型、源字段、目标字段、标准译法/禁用译法聚合，保留 occurrence、SKU 样例、观察候选和样例证据。其用途是把大量 SKU 级信号转成词条/字段级审核单元；观察译法永远未批准，不会自动编辑术语字典、目标字段或审核状态。manifest 绑定 CSV、源/目标 workbook 与术语字典 hash，审计 JSON 再绑定 CSV 和 manifest hash。逐 SKU 原始证据必须继续保留在 `QA_LOG.csv`，不能因为聚合而丢弃。
该入口同时输出独立 `QA_LOG.csv`，按 finding 生成稳定 `qa_id`，保留 SKU、字段、L1–L6 分类、review-only 属性、源/目标工作簿哈希、证据及完整 issue JSON；审计 JSON 记录 QA_LOG 文件哈希和行数。该 QA 日志属于审核元数据，不回写商品表，也不写入业务 `备注`。
L4 还读取已批准的 `term_dictionary.csv`，先合并规范化后同译法的等价词条、拦截同一源词的冲突映射，再统计词条在标题、规格、描述和详情中的源出现次数及标准译词出现率。材质、属性、服饰和规格类词条若同字段未出现标准译词，会生成 `APPROVED_TERM_CANONICAL_ABSENT_REVIEW` 人工复核项；它不是错译定论，因为目标可能用了同义表达，也不会自动改写。只有词典明确列入 `forbidden_zh` 的译法才会作为确定性硬 finding。该覆盖仍受已批准词条范围限制，不代表完整自由文本语义证明。
正式中文 Export Gate 从 canonical `localization_field_provenance` 读取字段级 provenance；严格发布要求批准状态之外还必须有非空 `approved_by`、带时区且可解析的 `approved_at` 和 `freshness_status=CURRENT/FRESH`。缺审批人/时间产生阻断 finding，未知 freshness 状态也阻断；旧兼容表不含这些审计字段，不得作为正式审批证据。provenance 同步以 canonical 表为读取优先级，防止兼容投影更新时清空已有审批证据；老式整行审批元数据会按字段投影，字段专属元数据优先。若字段级当前中文值与 `localization_patches` 的 `new_value` 完全相同，patch 的 source hash 与当前 product source hash 一致，存在 `PATCH_APPROVED` 和 `PATCH_APPLIED` 且最新事件仍为 `PATCH_APPLIED`，读取投影可从不可变事件恢复缺失的审批主体/时间并推导 `CURRENT` freshness；仅接受 `human:*`、`project-owner` 或经冻结 Stage 6 owner authorization 的审批主体，不把模型授权标签当作人工 Owner Review；现存非空元数据若与事件冲突则不覆盖。此恢复只补 provenance，不改变 review status；未批准字段依然阻断。该要求与字段 source hash 校验并行，任一不满足都不能发布。

正式中文 Export Gate 现在也接收同一正式字典里的已批准术语：命中 `forbidden_zh` 是阻断错误；材质/属性/服饰/规格标准译词缺失只写入 `review_findings`，不单凭规范译词缺失判错或阻断合法同义词。该复核项随 Export Gate 结果进入导出 manifest，`passed` 只表示没有阻断 finding，不等于 Gold 语义审核完成。标准导出和 Template 1 的 manifest/函数返回值必须显式包含 `quality_status`、`gold_status=NOT_CERTIFIED`、`gold_eligible=false`，并给出 `localization_review_finding_count`；即使正式导出可发布，Export Gate 也不得冒称完成了六层 Gold QA。
全量工作簿审计、Stage 5 候选 Guard 与正式 Export Gate 共用 `translation.approved_terms` 检查器：禁用译法阻断候选/正式导出；标准译法缺失则阻止 Stage 5 自动闭合并进入人工审核，在导出审计中作为非阻断复核项。审计报告和 Export Gate 结果记录检查器 SHA-256。Stage 5 候选身份绑定实际被 Resolver 选中的字典内容哈希，不能只绑定基线清单哈希；检查器实现也参与候选策略身份。
该检查器还会把“西语术语出现在源字段 A、其已批准中文术语只出现在目标字段 B”的情况标为 `APPROVED_TERM_CROSS_FIELD_REVIEW`。这是字段边界风险信号，不证明目标内容错误；Stage 5 必须阻止该字段自动闭合并生成审核项，全量审查与正式 Export Gate 记录非阻断复核 finding，不自动搬回或改写内容。此规则仅覆盖已批准词典中的术语，不构成全部自由文本的跨字段语义证明。
对于材质/属性/服饰/规格词典，如果中文候选含有某条标准译词、但该西语词条在该 SKU 全部标题/规格/描述/详情源字段中均未出现，则记录 `APPROVED_TERM_UNSUPPORTED_TARGET_REVIEW`。这可能是模型新增属性，也可能是源词同义表达未收录，因此只阻止 Stage 5 自动闭合并要求复核；全量审计/Export Gate 只记 review finding，不自动判定幻觉或改写。
审计按 `config/stage5/chinese_gold_qa_policy.json` 的固定 L1–L6 规则归类 findings，并在报告记录策略哈希；未登记的 finding 必须落入
`UNCLASSIFIED` 并显式显示。策略中每层的 `covered_checks` 与 `not_covered` 必须为有效字符串列表，且 `covered_checks` 不得为空；声明 `COMPLETE` 时 `not_covered` 必须为空，避免仅修改状态标签或清空检查列表就放开 Gold 门禁。只有每层均标记 `COMPLETE` 且未覆盖项清零后，策略才允许设置 `gold_claim_allowed=true`；审计结果还必须无 finding、无未分类代码才会给出 `full_gold_eligible=true`。当前该 workbook audit 六层均标记为 `PARTIAL` 且 Gold 声明关闭，报告即使“范围内无发现”也不得宣称 FULL GOLD。
L1 会在两份工作簿都含对应列时对账当前价、按导出规则显示的原价、商品链接和图片链接；字段列只在一侧出现时记为 schema finding，
两侧都没有时报告 `NOT_PROVIDED`，不伪装成已检查。
L2 使用 `--category-dictionary` 指定的分类字典，只采纳显式 `HUMAN_REVIEWED/APPROVED/CONFIRMED` 等已批准状态；
`CAT1_CONFIRMED` 只可确认一级类目，不能据此接受二级类目映射。未映射、冲突和实际译值不符分别报告，不从频次表自动推断。
全量工作簿审计对普通新品/促销/可持续状态，在西语源表提供结构化布尔列（`is_new_badge`/`action_new_badge`、`promotion`/`promotion_active`、`sustainable`/`sustainable_badge`）时以其为准，并逐 SKU 对账中文导出备注实际呈现的状态；仅旧版西语工作簿没有结构化列时才回退解析源备注。结构化列只要部分缺失或值无法解析，就报 QA finding，不以备注文本静默补猜。官网官方标签仍从 `raw_tags` 或明确标记的官方标签文本核对，中文备注需保留西语官方标签原文。工作簿审计不能替代采集证据及正式生产 provenance 的独立核验。

中文商品字段出现已知内部 QA/修复过程用语（如“原文未注明数量”“字段边界修复”“QA说明”）时，
Stage 5 和正式中文发布门禁以 `INTERNAL_QA_NOTE_LEAKED` 拦截；该说明应进入审核证据，不能混入
商品描述或详情。

同字段西语出现明确否定而中文没有否定表达时，或中文新增明确否定时，Guard v3 分别以
`NEGATION_DROPPED` / `NEGATION_HALLUCINATED` 标记并阻止候选自动通过。中文出现“无硫酸盐/不含硫酸盐”等
特定 free-from 声明时，还必须在同字段西语源中有明确 `sin/libre de/no contiene sulfatos` 或 SLS/SLES
依据，否则以 `UNSUPPORTED_NEGATIVE_ATTRIBUTE` 判为无源属性新增；例如“纯素配方”不能推出“无硫酸盐”。
该检查是版本化的窄词项规则，不泛化到所有中文“无/不”表达。该守卫只做保守提示，
不自动改写；否定形式识别覆盖常见等义表达（如 `sin dejar marcas → 不留痕迹`、`no sueltan pelusas → 不易掉絮`、`no se pega → 不粘`、`no resbala → 防滑`、`no amarillea → 不易黄变`、`sin granos → 无谷物`、`sin conservantes añadidos → 不添加防腐剂`、`Inborrable → 不可擦除`、`no es necesario → 无需`、`sin tener que ensuciarse las manos → 不易弄脏双手`、`invisibles/no sobresalen con el calzado → 穿鞋后不易露出`、`nunca más → 再也不/不会再/永不/从此不/不必再/不需要再/无需再/不用再`、`sin irritaciones → 减少/避免/防止……刺激`、同字段 `Se acabaron... → 告别/对应问题消失`、`0% de alcohol → 0%酒精`、`sin aditivos artificiales → 无人工添加剂`、`sin vello → 无毛`、`no desprende olor → 无气味/无异味`、`sin mangas → 无袖`、`sin talón → 无后跟`、`se mantienen en su sitio → 不易滑落`、`excepto → 不包括`），并排除 `sin esfuerzo/complicaciones`、`no pueden faltar`、`no te costará nada`、`nunca se tienen suficientes`、`no solo…sino también`、`nada menos que`、`sin importar` 等正向习语、强调语和疑问式修辞，以及“必不可少/不可或缺”等中文正向固定表达。否定同义规则必须有对应源语触发：例如 `nunca más` 允许“再也不”等持续否定表达，但“减少丢失”不能替代“永不丢失”；`sin irritaciones` 可匹配“减少剃须后的刺激”等带修饰语的表达；`Se acabaron...` 可匹配“告别坚硬表面”或“让鞋子异味消失”；`hermético al aire y los olores → 防漏气/防串味`、`no se queme → 防焦`、`antirrotura/anticarreras → 防勾丝` 均需匹配同字段触发。`sin mangas` 可对应“无袖”，但 `sin tirantes` 是无肩带，不可借此规则映射成无袖。西语 `0% de alcohol` 翻为“不含酒精”时允许省略数值；若中文显式保留 `0%酒精`，则数字和百分比仍必须按原值对齐，不能当作已省略。词项必须按上下文识别：`inodora → 无香` 是无气味属性，但 `inodoro` 是“马桶”；中文 `难以`、“防油”等词不单独等价于否定，`no llegar/no arranca/sin grasa` 等只按显式源语短语绑定识别。西语肯定属性 `opaco` 与中文“不透明”按源语语境等值，不作为否定新增；但源语透明而中文写成“不透明”仍会触发反向检查。保护功能词和材质词（如 `protege → 防止`、`antiadherente → 不粘`、“不锈钢”）不作为普遍否定证据，避免把正向属性误判为新增否定。该规则仍是整字段保守信号，不证明否定在句间逐项对齐；Guard PASS 仍不代表语义审核通过。
补充的源绑定否定回归覆盖：`Sin sal → 不加盐`、`sin tostar → 未经烘烤`、`Pilas AA no incluidas → 需另配 AA 电池`、`con o sin pedal → 脚踏桶或普通垃圾桶`、`no se decolora → 不易褪色`、`no se desliza → 不滑动`、`sin fragancia/sin microplásticos → 无香精/无微塑料`、`sin que ocupe mucho espacio → 不占空间`。正向修辞如 `No hay nada mejor que...`、`sin preocupaciones`、`no pasa nada` 不应被当成事实否定；`Inoloro: Sí` 与 `Inoloras` 可按无味/无香的字段语境匹配，但不得把 `Inodoro: Sí`（马桶）推广成无异味。以上是窄范围同字段规则，不放行未匹配的否定，也不自动修正文案。

后续回归还覆盖结构化事实表达：`Sin cierre → 无闭合`、`Ninguna ocasión → 无特定场合`、`Sin silicona: Sí → 无硅油：是`、`Sin ácidos → 无酸`、`sin arañazos → 无划痕`、`sin procesar → 未经加工`、`sin calor → 免加热`、`sin calorías → 零热量`、`sin metal → 无金属`、`sin aros → 无钢圈`、`sin disolvente → 无溶剂`、`no deja sensación grasa → 不油腻` 和 `no deja marcas blancas → 不易留下白痕`。`antigoteo/a prueba de fugas` 属于正向防漏属性，不作通用否定证据；只有同字段明确 `no deja pasar la humedad/ni una gota` 时，才允许相应 `防潮/防水` 等表达。`sin embargo`、`a lo mejor no`、`no te preocupes`、`sin duda` 等按语法/语用上下文排除，而 `sin límites` 等仍需保留事实边界审核。

否定/安抚语句继续按语义边界区分：`no tengas que preocuparte por el riesgo de incendio → 无需担心火灾风险` 是需源支持的产品安全信息；`No te preocupes`、`sin preocupaciones` 等安抚修辞可对应“别担心/无忧”，不作为事实否定。指令式 `no te olvides de retirar...` 对应“记得/需撕下……”，`no importa qué...` 对应“无论……”，以及 `el techo tampoco se escapará de la limpieza` 对应“天花板也能清洁到位”，都不应被当成属性否定。中文“无需担心”仍属显式判断，缺少同字段西语依据时继续报 `NEGATION_HALLUCINATED`。

另补直接对应：`nada se te resbalará de las manos → 不易从手中滑落`、`nada se pega → 不易粘锅`、`evitar el uso innecesario de bolsas de plástico → 减少一次性塑料袋使用`、`se adhiere menos a la piel → 不易粘附皮肤`；均需同字段来源触发，无源反例继续报 hallucination。时间表达 `en nada de tiempo` 与 `nada más llegar/entrar/salir` 不属于事实否定；这只排除否定语义误报，不代表可忽略其他字段的内容完整性审查。

中文否定等值词表另覆盖 `sin durezas → 无老茧/无硬茧` 与 `sin burbujas → 无气泡`，避免常见汉语词形差异被错记为否定遗漏；同字段没有相应西语否定时，“无老茧/无硬茧/无气泡”仍须按新增否定检查。该次 9/28 全量复审 v35 将两条等义误报从 `NEGATION_DROPPED` 中移除，但不代表剩余 finding 自动闭合或工作簿已修复。

再补词形等值：`sin cable(s) → 无线`、`sin polvo → 无尘`、`sin bolsa → 无尘袋`、`sin cinta de ajuste → 无调节带`、`sin que se deshagan → 不易松散`、`sin que se borre → 不易擦除`。其中“无线”不能作为全局否定词，因为西语正向形容词 `inalámbrico` 和技术术语 `True Wireless Stereo` 同样能支持中文“无线/真无线立体声”；Guard 需保留源绑定条件，只在源文确有 `sin cable(s)` 时将其视为否定等值，并允许上述明确正向源词，不得因目标词“无线”在无关/相反源文下静默通过。对应词形扩展均需有源支持与无源/相反源反例。

另确认并纳入 `sin polvo → 不易扬尘`、`sin salpicar → 不飞溅`、眼线产品语境 `sin dejar manchas → 不易晕染`。`no gotea/antigoteo/antiderrames/a prueba de fugas/protección contra las fugas` 可在同字段支持“防漏”，同时无来源的防漏断言仍需报出。不可把 `sin dejar marcas → 减少水痕` 一概放行为等义，因为它可能弱化原文“无痕”承诺；这种强度差异继续交给 QA 复核。

新增词形识别：`sin bolsa → 无袋`、`no deja ningún residuo → 无残留`、`sin huellas dactilares → 无指纹`、`sin cables → 免插线`、`no deja burbujas → 不起泡`；`no hay problema/没问题` 按常见正向修辞排除，不作事实否定。无来源的“无袋/无残留/无指纹/免插线/不起泡”仍须被识别为候选否定并接受 QA，不得因本次等值映射而默认通过。

后续词形/上下文回归还覆盖：`sin que falte la luz → 不遮挡光线`、`sin menta → 无薄荷`、`sin sustancias nocivas → 不使用有害物质`、`sin vitalidad → 缺乏活力`、`no deja el cabello graso → 不使头发油腻`、`no hay nada más molesto que se bajen los calcetines → 袜子不易下滑`、`no debería faltar → 少不了`。将 `metales no ferrosos` 和名称 `No Worries` 作为非否定文本；`No Worries` 是产品线名称，不能触发属性负向 QA。也排除操作条件/CTA/修辞（如 `no lo utilices`、`no te pierdas`、`no dudes en`、`no hay problema`、`sin más`），避免把说明语气当成产品属性。

上下文等值仅在源文触发时通过：相框源句 `sin que el marco te distraiga` 可由“简洁设计、突出照片”承接；手机挂绳 `sin tener que meterlo en los bolsillos` 可由“腾出双手/手机随手可用”承接；运动袜“不易下滑”可承接“袜子滑落会分散运动注意力”的同段表达。目标端的“少不了/不遮挡光线/不易下滑”等若在无来源或相反来源中出现仍需作为负向声明检查。等义词守卫仅减少已验证表达的误报，不替代描述事实完整性审核。

v45–v48 同字段复核还增加了窄范围词形/上下文对应：`no hace mucho ruido → 低噪音/声音较小`、`sin que nadie pueda verlo → 隐形墨水`、`no coja olores indeseados → 避免异味`、`no dañan el cabello → 不易损伤头发`、`sin resecarlas → 不使皮肤干燥`、`no se rompen en caso de caída → 不易破损`、`para que el flujo de aire no lo levante → 避免烘焙纸被气流吹起`、无 3.5mm 耳机插孔且仅支持 USB-C 的源句、`se mantienen en su lugar → 不易移位`、`sin dejar marca → 不易勒痕`、`no se caliente demasiado → 不易过热` 和 `rizador de pelo sin calor → 无热卷发器`。钥匙/手机 `no perder...tan fácilmente / no volver a perder...` 可承接“减少/不易丢失”，但 `nunca más perderás la maleta` 绝对承诺弱化成“减少丢失”仍须报 `NEGATION_DROPPED`；同样，“减少丢失”必须有源文丢失事实支撑。以上是源句与目标词形的有界对应，不允许单独把中文同义词变成全局安全词。

另将 `¿No te gusta...?`、`¿No sabes lo que quieres hacer...?` 这类反问，`por si no te apetece...` 偏好表达，酒店品质比较修辞、`no es gloria del pasado` 以及“不要一口气吃完”式说明/俏皮话按精确句式排除出事实否定检测。此豁免仅排除否定 polarity finding，不豁免客观事实、数字、功能、兼容性或描述边界 QA；不得泛化成忽略其他 `no/sin/nunca` 句子。

标题/规格守卫同时保护 `XL/XXL/XXXL` 和 `7-in-1` 一类功能标识；`7合1` 与西语数字功能标识按同一
token 对齐，避免只因本地化连接词而误报，同时确保功能标识及尺寸等级不丢失。补充的 `2 en 1` 语义复核必须复用同一 token 归一化，因此“二合一”“两合一”“2合1”不因阿拉伯数字/中文数字差异触发错误的人工复核。
标题 Guard 还将 Nintendo Switch（允许移除相邻 Nintendo 品牌词，但必须保留 Switch 平台）、PlayStation/PS3–PS5、Xbox 系列以及 Windows、iOS、Android/安卓、macOS、ChromeOS 作为兼容平台事实；遗漏或无源新增会触发 `TECH_TOKEN_DROPPED` / `TECH_TOKEN_HALLUCINATED`。裸小写 `switch` 不当作游戏平台，减少普通词误报。该规则只是已知平台清单，不证明全部兼容对象语义均已覆盖。
数字 Guard 仅按同字段源证据对齐明确的词汇化数量：`1 plaza → 单人`、`2 personas → 双人`、`4 estaciones → 四季`、`1 tamaño; vale para todos → 均码`；不把未明确计数的普通语句提升为硬数字。同一数字在源字段被重复陈述时，若目标保留全部不同数值、没有引入新数值且仅减少重复次数，不因复述次数差异判数字丢失；数值变化/新增仍拦截，单位和技术 token 另行比较。比较数字时，逗号/点小数规范为数值字符串并去除不改变数值的尾随小数零（如 `4,0 → 4`、`4.00 → 4`）；千位分隔仍按既有 locale 规则处理。该规则只消除表示精度差异与重复提及造成的计数误报，不证明数字所对应的语义对象已完整对齐。中文 `N支` 只在同字段西语源中存在可对齐的显式 N 时作为数字证据；普通“一支牙刷/一杯茶”不被视为明确数量。单位 Guard 将 `gsm`、`g/m²` 与 `克/平方米` 识别为同一克重单位，将 Unicode `℃` 与 `°C/ºC` 识别为同一温度单位，并在西语属性键明确写出 `gramos por m²`、值只写 `g` 时结合键语境比较。源冲突 QA 还对 kg/g/mg、L/cl/mL、m/cm/mm/inch 进行确定性等值换算，仅用于比较，不转换或改写源事实；未覆盖的表达仍需人工审核。

技术 token Guard 对同一型号/标准/接口的重复提及按身份集合比较：若所有不同 token 均保留、目标无新增 token 且只是减少重复次数，不判为技术事实丢失；任何一个不同的型号/接口/平台遗漏仍阻断。句点等末尾标点不属于 token 本体。单位 Guard 对 `mm2/mm² ↔ 平方毫米`、`gr/m2 ↔ 克/平方米`、`kWh ↔ 千瓦时` 使用版本化确定性映射；`kWh/1000h` 中的 1000h 仍独立作为时间单位核验。面积单位的指数 `2` 不计作额外产品数量；`125 mm` 后换行的下一条数量不能被合并为 `mm2`。这些规则只规范已列出的单位拼写，不进行跨量纲换算，也不把未知技术 token 自动降为普通文字。

## 8. Export 规则

### QA-EXP-001：正式来源

正式 Export 只接受 QA PASS/PASS_PRESENCE_ONLY + FULL_COMMIT；拒绝 dry-run、QA FAIL 和未提交 staging。

### QA-EXP-002：当日 SKU 集合

当日清单以正式有效 Listing/CURRENT 集合为准，不以 Sitemap 数量为准。

### QA-EXP-003：三表对账

Template 1 必须满足：

```text
第一张表当日为 1 的 SKU 集合
  == 今日西班牙语清单 SKU 集合
  == 今日中文清单 SKU 集合
  == CURRENT_VALID SKU 集合
```

### QA-EXP-004：ES/ZH 事实一致

ES/ZH 的 SKU、顺序、当前售价、原价、图片链接和商品链接逐 SKU 一致。

### QA-EXP-005：中文缺失

中文缺失保留 SKU，以 fallback 和精确待审核标记处理。禁止为了得到“纯中文”而删除商品。

### QA-EXP-006：历史 Presence

每个日期的 0/1 只来自该日期源批次唯一 SKU 集合。不得由 first_seen/last_seen 推断。

### QA-EXP-007：图片

只有中文表嵌入本地 250×250 白底图片；缺图只标记，不阻断 SKU 输出。Export 不负责下载。

### QA-EXP-008：只读性

导出前后 Master、State、Dictionary 和历史源文件内容/hash 不变。

### QA-EXP-009：官方标签身份

中文备注可显示已解析的新品、促销、可持续业务状态；源 `raw_tags` 中每个非折扣标签都必须保留在官网官方标签段，
已知类型附中文释义，未知类型保留原文而不猜译。折扣百分比由结构化折扣字段表达。正式 Export Gate 与全量工作簿审计都必须逐 SKU 核验标签身份和原文。
不得只用通用业务状态取代原始官网标签。

### QA-EXP-010：品牌与中文品名解耦

品牌字典只生成独立品牌字段/品牌审核状态，不因品牌已确认就自动将品牌名或“牌”字
拼接进中文品名。中文品名仍以该 SKU 已审核、与当前西语来源绑定的字段值为准；型号、系列、
IP、兼容平台、接口和技术标准仍按字段事实规则保留。需要把品牌写入品名时，应作为品名字段
本身的人工/可信审核决定，而不是导出时的格式化副作用；当前尚未实现品牌例外机制，任何标题品牌保留都不能通过通用导出豁免。

### QA-EXP-011：NO_BRAND 标题策略

中文标题遵循 `config/stage5/title_display_policy.json` 中的版本化 `NO_BRAND_TITLE_V1`：删除已确认商业品牌，
但不得删除型号、具有技术/规格含义的系列代号、技术标准、接口、兼容平台、尺寸等级、容量、剂量、颜色、数量和功能事实。描述和详情中的品牌、
IP、系列及认证按源事实保留。策略哈希进入 Stage 5 pipeline identity；策略文件缺失、字段不完整或包含未实现的
例外时必须 fail closed。不得通过通用导出 exception 绕过标题品牌门禁。
当前品牌字典尚无经批准的中文别名字段；若源标题确认有品牌、候选中文标题仍出现“中文词 + 牌 + 商品名”形式，Stage 5 将以
`UNRESOLVED_CHINESE_BRAND_MARKER` 阻止自动纳入候选，而不是猜测或删除该中文词，必须人工复核。该检查是保守复核信号，
不是中文品牌识别的完整证明。

正式中文导出逐字段复用 Stage 5 Guard：`INVALID_CATEGORY`、中西语残留、必需源字段被清空、数字/单位/技术 token、
认证、否定和内部 QA 文案等硬错误均进入 release findings；严格正式模式必须阻断。已确认品牌短语按 source-hash-fresh
品牌关系提供给 Guard，避免把描述/详情里按合同保留的品牌误判为普通残留；标题仍由 `NO_BRAND_TITLE_V1` 单独检查。

Stage 5 inference contract v2 明确自由文本不得摘要或省略客观产品事实；允许压缩营销修辞，但材质、颜色、套装数量、功能、兼容性、认证、警告和使用限制必须保留在同字段，且不得跨字段搬运。合同哈希变化后，旧候选仍按其原合同审计，不得冒充由 v2 生成；新候选需重新生成并继续人工审核。该提示词约束不替代语义 QA，当前模型 Guard 对非数字语义事实仍非完备证明。
当前 Stage 5、只读全量审计和正式发布门禁还会调用窄范围 `source_fact_repair` 检查器，发现标题型号、`2 en 1` 功能或单只袜子/一双等已知风险时，仅附证据并以 `SOURCE_FACT_REVIEW_REQUIRED` 阻止直接放行；绝不自动改写。该规则集不是完整语义证明，其他材质/颜色/客观事实遗漏仍需人工 Gold 审核。
标题型号提示会排除 source title 中与已确认品牌字典精确匹配的 token，避免 `GS27`、`K2` 等依 NO_BRAND_TITLE_V1 应删除的品牌被误报为缺失型号；仅消除这个特定模型 token finding，不允许品牌进入中文标题，也不改变描述/详情品牌保留规则。品牌字典缺失或未确认的短语不获得排除资格。
数字守卫只增加有明确源边界的等义：西语 `uno/una ... el/la otro/otra` 成对变体可对应中文两次“一+量词”，以及 `diez meses` 等明确文字数字加时间单位可对应 `10个月`；不把普通“一款/一杯”等中文文章泛化为源数量。
描述压缩风险使用 `config/stage5/description_fidelity_policy.json` 里的版本化阈值，由全量审计与正式中文发布门禁共用；命中只作审核信号且不自动改写，策略 ID/hash写入相应报告。字符比例只是风险启发式，不能据此判定语义完整或错误。

## 9. 发布门槛

正式发布前至少验证：

1. 完整回归测试通过；
2. 历史数据 dry-run 通过；
3. 同日期重复执行幂等；
4. 一次真实完整 run 达成 QA PASS；
5. export preview 三表对账通过；
6. manifest 数量、hash 和图片统计正确；
7. 没有把本机 runtime、密钥、Cookie、图片或正式 Excel 提交到 Git。

## 10. CI 门禁

### QA-CI-001：安全测试范围

CI 只运行标记为 `CI_SAFE` 的本地测试，测试数据使用临时目录或内存 fixture；真实官网采集、浏览器安装与交互、图片下载和模型网络调用不属于 CI。

### QA-CI-002：依赖可复现

CI 使用 Python 3.12 和仓库中的 `requirements-dev.txt`。新增运行时依赖时必须同步更新依赖文件、文档和测试。

### QA-CI-003：无生产副作用

CI 不得修改 Master、State、Dictionary、历史源文件或 `runtime/` 生产证据，也不得执行 baseline 发布、push 或 merge。

### QA-CI-004：门禁含义

CI 全绿只表示代码回归测试通过；正式运行仍必须经过本地 dry-run、QA、正式提交和导出预览。

## 11. 2026-09-30 否定 Guard 回归基线

否定等义必须由当前字段自己的西语来源支持；禁止用通用“否定同义词”直接消除 finding。`sin tirantes` 只可对应“无肩带”，不可映射为“无袖”；`sin interrupciones` 可对应“不间断”；源文明确表示去除、预防或中和异味时，目标相应的除味表达可视为有来源依据。无源反例必须继续报 `NEGATION_HALLUCINATED`。

Guard 不放宽语义强度：`nunca más perderás la maleta` 被弱化为“减少丢失”、`sin dejar marcas` 被改成“减少水痕”仍须保留 `NEGATION_DROPPED`。同样，源字段没有依据的“不加盐”“无缝”“无需养护”仍须保留为新增否定/属性候选。命中 `NEGATION_*` 是保守复核信号，不是自动翻译或修复；Guard PASS 也不等于语义审核通过。

9/28 固定工作簿 v52 基线：5,496/5,496 SKU 匹配，`NEGATION_DROPPED=145`、`NEGATION_HALLUCINATED=12`、`full_gold_eligible=false`；源表/中文表哈希绑定至 `dec4bfe2ee0009406de6e65b40c6648e771eca3f9cb80fe310e1dbf5fb0d4e79` / `cd773ec10b928d9b0e02c1af72130d20307998a6225b84c81eaa2af8832a6852`。此基线只读，不修改工作簿；总 finding 包含相互重叠的复核信号，不可解释为确定错误数。

异味属性需区分动作强度：同字段 `eliminar/neutralizar` 不应被“减少异味”弱化，`prevenir/evitar` 不应仅翻成“减少异味”；该类目标词出现时以 `NEGATION_DROPPED` 留待复核。`no elimina ... malos olores` 被翻成肯定去除效果则属于 `NEGATION_HALLUCINATED`。v53 审计在同一只读数据上报 `NEGATION_DROPPED=153`、`NEGATION_HALLUCINATED=12`，暴露出 8 条此前未被强度规则捕获的候选；这不是 8 项已修复。源/目标表哈希不变，完整回归 664 项通过，Gold 仍不通过。报告在 `%TEMP%/action-localization-audit-negation-coverage-v53-20260930/`。

2026-09-30 详情键词典 v5 将高频、语义明确的结构化属性键纳入版本化映射，并以源西语键为准规范候选中文键；只改键，不改值、顺序或重复字段。工作簿观察到的同义键别名只用于修复已一一对齐的键值对，不代表整个产品详情语义已验收。`Cantidad` 的“包装数量”不列为“数量”的自动接受别名，必须继续人工/源上下文复核。v55 同表审计详情键覆盖为 21,193/39,981（53.01%），仍有 1,453 个未映射源键、11,975 个未规则覆盖的源键值对；键值规则覆盖不能作为翻译准确率。v55 共有 1,278 个 `DETAIL_KEY_RULE` 修复候选（946 SKU），审计仅列出 `proposed`，`auto_apply=false` 且未写工作簿；新增 finding 不等于确定错误。全表 `full_gold_eligible=false`，Gold 仍未通过。

在用户明确要求继续修复后，v56 将上面的 1,278 个源绑定键名候选应用到独立的中文修复副本（946 SKU），仅规范 K 列产品详情的属性键，不改值、顺序、重复字段及其他单元格；原始西语源与原中文候选工作簿均未覆盖。复核副本后 `DETAIL_KEY_RULE` 为 0，SKU 对账仍为 5,496/5,496，Gold 仍为 `false`。该批处理仅针对键名，不证明产品详情值、描述、类目、标签、数字或全表语义问题已修复；v56 完整报告及 QA_LOG 绑定于修复副本哈希。

2026-09-30 结构化详情键回归：西语 `Cubierta: Cubierta blanda/dura` 必须映射到中文 `封面：软封面/硬封面`；`Parte de la batería` 必须映射到 `电池化学体系`。不得将前者误写成“平装”“装帧方式”“外罩”等，也不得把后者误写成“电池类型”；不得覆盖独立的 `Tipo de encuadernación`（该键负责 Paperback、螺旋装订等绑定方式）。修复器按源详情键位置对齐，只替换对应键值，保持重复键、顺序、数值和分隔符；无法一一对齐时 fail closed，不自动猜改。

2026-09-30 结构化详情值回归：西语 `Relleno de página` 按源位置统一为中文键 `内页格式`，`Con líneas/Blanco/Cuadrícula` 只映射为 `横线/白色/方格`，不得把同一字段值改成营销描述或删除并列值；西语 `Material: Polipropileno (pp)` 只在对应材质条目中保留并规范为 `聚丙烯（PP）`，不得重排或覆盖其它材质。官网字段无冒号（如 `Cubierta Cubierta dura`）也必须纳入同一源键识别规则。多字段落在同一产品详情单元格时，修复器必须先合并再单次写入，避免后一个补丁覆盖前一个补丁。v69 定向回归要求：`Relleno de página` 49/49、PP 材质 89/89、电池化学体系 37/37、封面 77/77 均无残留；该门禁是字段级回归，不等同于 Gold 全量通过。

2026-09-30 结构化详情分隔符与键规范：详情位置对齐必须同时支持中文分号 `；`、ASCII 分号 `;` 和竖线 `|`；不得因分隔符变体跳过字段修复。`A color` 统一为 `是否彩色`；`Apto para tipo de animal (de compañía)` 统一为 `适用宠物`；`Uso previsto` 统一为 `用途`；`Pilas incluidas`、`Con tapa`、洗碗机/微波炉适用性统一使用版本化的“是否…”键；营养键、线缆长度和储存建议按 `detail_terminology_rules.json` 的规范键处理。只修改源字段绑定的键片段，复制现有目标值，不重翻值、不改变条目顺序。对于 `Sustancia: Válido` 等已登记的源异常过滤，条目数差异必须在 QA_LOG/SOURCE_ANOMALY 中可追溯，不得将其误报为 SKU 缺失。
