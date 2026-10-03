# Action SKU Tracker 当前状态

更新日期：2026-09-30
项目目录：仓库根目录
当前分支：`fix/naming-history-export-20260917`

- 2026-10-03 审核包程序补齐长详情 CSV 字段长度限制，并按聚合 `candidate_id` 校验 V2 决策模板；保留旧 occurrence-level 模板兼容。空决策/失败提案结果显式标记 `rules_write=false`。只修改审核程序与回归测试，不修改任何工作簿、Master、SQLite 或正式字典。
- 2026-10-03 导出模块根因修复：西语 Export Gate 现在识别二级类目源空值、拒绝无源猜测并保留官网源异常审查；详情解析/本地化 source hash 统一为语义格式（`SOURCE_HASH_V2`），兼容旧哈希；分类字典增加一级+二级 pair 优先和歧义保护。未修改工作簿、Master、SQLite 或正式字典；全量回归 726 passed。

## 2026-09-29 本地化 QA 修复进度

- 2026-10-01 治理链路继续加固：详情审核模板改用聚合 `candidate_id`，候选保留商品语境；批准规则必须绑定版本化回归 case，空白决策表 fail-closed。新增批准提案到逐 SKU/逐详情位置 repair manifest 的只读展开器，统一交给既有 `build_preview` 做 source/target hash、详情结构和政策校验；Stage 5 源异常按实际受影响字段路由，Export Gate 的源异常豁免精确到 anomaly code。未修改任何工作簿、Master、SQLite 或正式字典；完整回归 `718 passed`。
- 2026-10-01 使用现有 5,496 SKU 西语源与 v85 中文候选做只读 v86 全量审计：SKU 对齐 5,496/5,496，findings 7,666，详情审核队列 13,568 行，聚合候选 13,565 行且全部带语境；Gold 仍为 `false`。空白 V2 决策表被 `DECISION_LEDGER_EMPTY` 阻断，未生成规则提案，未修改输入表格。

- 已修复 Stage 5 在发现已批准术语疑似跨字段迁移、且需新建 Review Queue 行时的 `review_id` 哈希入参错误；现在候选被拒绝、审核记录和失败记录均可确定性生成。
- `APPROVED_TERM_CROSS_FIELD_REVIEW` 已在 Gold QA policy 归入 L5，并明确为人工复核信号，不据此自动改写或直接认定错误。
- 新增 `APPROVED_TERM_UNSUPPORTED_TARGET_REVIEW`：当中文出现已批准材质/属性/服饰/规格术语，而对应西语词条在该 SKU 六字段范围内完全无命中时，Stage 5 以 `SEMANTIC_REVIEW_REQUIRED` 阻止自动闭合；审计与 Export Gate 输出非阻断复核及计数。该提示不等于确认幻觉，因为西语同义表达可能尚未入词典。
- Stage 5、只读全量工作簿审计、正式 Export Gate 的跨字段术语检查均新增集成回归；Export Gate 检查为非阻断 review finding，Stage 5 则阻止候选自动闭合。
- 固定详情错译回归已补充 Ave→鸟类、Fosa→墓地、A color 否定值及既有 Con líneas/洗涤次数/软封面/fiction/非一次性/PP 规则覆盖，防止只修报表 SKU 而漏掉同类规则。
- 认证与否定事实已有双向回归（FSC/BPA 的丢失和无源新增），并覆盖 Stage 5、全量工作簿审计和 Export Gate。
- 全量只读本地化审计现在同时生成独立、稳定 ID 的 `QA_LOG.csv`；审计 JSON 绑定 CSV 哈希和 finding 行数，审核 findings 不回写商品备注或原工作簿。
- 正式导出与 Template 1 的 `QA_LOG.csv` 同样保留重复 finding；增加稳定 occurrence 序号避免相同内容哈希出重复 `qa_id`，新增乱序/重复回归，业务备注继续只保留业务状态和官网标签。Template 1 现将工作簿、QA_LOG 和 manifest 通过备份/回滚路径成组发布，避免部分替换造成审计配对不一致。
- 已补正式 `export_catalog` 集成回归，证明正式导出从字典加载批准术语并将无源依据属性 finding 写入 manifest；Template 1 路径也显式传入同一术语集。
- 修复 QA 提前返回路径遗漏源异常摘要的问题：BLOCKED、访问受限 FAIL、无效观测 FAIL 与正常结束现在都保留同一 `source_consistency_review` check 和 finding 计数，不改变 Presence/Lifecycle 决策。
- 新增日常 QA→Snapshot 集成回归：配置的官网源异常保持 review-only，不改变 Presence QA 结果或原始西语值；`qa_report.json` 持久化完整六字段、source hash、规则 manifest、字段级异常 evidence、OPEN_REVIEW 状态与不改源值动作，并验证提前返回也保留 findings。
- Snapshot 现在另外输出独立 `SOURCE_ANOMALY.csv` 和 `SOURCE_ANOMALY.manifest.json`：每条字段级异常 evidence（包括重复键值的多次出现）有稳定 ID、SKU、原字段/键值、源字段快照、source hash、规则 manifest、状态和动作；manifest 绑定异常 CSV 行数/哈希与 qa_report 哈希，Snapshot 写入后立即自检 schema、唯一 ID、行数及哈希，失败关闭；零异常仍输出带表头空清单。新增有异常、重复异常、无异常及文件篡改的回归，不改变 Presence QA 或源事实。
- 加固 Gold QA policy 校验：每层必须声明非空 `covered_checks`；任何标为 `COMPLETE` 的层必须同时清空 `not_covered`。新增反例测试，防止仅改 COMPLETE 标签或清空检查列表开启 Gold。当前配置仍是 `gold_claim_allowed=false` 且六层 PARTIAL；未分类 finding、任意 finding 仍会使 `full_gold_eligible=false`。
- 标题事实 Guard 新增兼容平台 token，品牌删除时仍保护 Nintendo Switch、PlayStation/PS3–PS5、Xbox、Windows、iOS、Android/安卓、macOS、ChromeOS；Stage 5 Guard 和全量工作簿 QA 均有遗漏/新增/别名回归，并避免普通小写 switch 误报；Gold QA L3 明确登记平台覆盖范围。
- 统一“2 en 1”功能 token 与语义复核口径：`二合一`、`两合一`、`2合1`均可与西语功能事实对齐，避免硬字符串比较把正确翻译误送人工审核；漏译仍会产生审核建议且不自动改写。
- 详情词典新增 `closed_enum_keys` 合同：指定的封闭枚举源值未命中版本化映射时，Stage 5 在模型请求前转人工队列（不消耗模型调用），正式 Export Gate 阻止严格发布，全量工作簿审计记录 L4 finding；保留源文、不自动猜译。
- 收敛 Stage 5 失败分类：未映射封闭枚举显式归类为 `SEMANTIC_REVIEW_REQUIRED`（P2 人工审核）；新增受配置约束的 fallback，未知内部路由原因不再泄漏到对外 `failure_type`。定向回归覆盖具体枚举分类与未知路由 fallback。
- 修复全量审计详情词典覆盖率报告未生成的问题：覆盖统计已返回到审计报告，并按每个商品标题语境核验上下文限定的字段/枚举规则；新增未映射 SKU 样例、覆盖/未覆盖 occurrence 数和语境错配回归。现在同一西语键值即使规则覆盖完整，只要现有中文译法分裂，也会按译法列出 occurrence、SKU 样例及规则覆盖状态供复核；该报告不自动判错或改写。
- 将详情覆盖统计落为完整审核队列：`audit_chinese_localization.py` 现在生成 `DETAIL_RULE_REVIEW.csv` 和绑定源/目标/词典哈希及行数的 manifest，列出所有未覆盖键值与有分歧的已覆盖键值；JSON 仍保留精简摘要，CSV 不受前 200/300 条截断。候选只展示工作簿中已观察的中文译法，稳定 ID、`candidate_is_approved=false`，禁止自动应用。测试覆盖语境冲突、已覆盖但译法分裂、重复生成确定性和超过 300 条不截断。
- 类别 QA 增加规则级 `CATEGORY_RULE_REVIEW.csv`：将逐 SKU 的未映射、冲突和不匹配按源一级类目/二级类目及候选目标一级类目聚合，同时保留 QA_LOG 原始 finding；观察候选永不自动批准或写入类别字典。对 9/28 5,496 SKU 工作簿重跑，207 条 `CATEGORY_MAPPING_UNAVAILABLE` 收敛到有作用域的复核行，另包含 15 条已知映射不匹配；共 20 个类别规则复核项。CSV/manifest/审计 JSON 的文件哈希、源/目标表 hash、字典 hash 和行数核对通过。该只读复核仍不修改源表或正式字典。
- 术语 QA 增加 `TERM_RULE_REVIEW.csv`，按词条、术语类型和源/目标字段聚合 canonical 缺失、疑似跨字段、无源依据候选和禁用译法信号。9/28 同一审计中 18,128 条已批准术语相关 finding 收敛为 213 个规则/字段复核项；仍在 `QA_LOG.csv` 保留全部逐 SKU 原始证据。复核行按源/目标 workbook 与术语字典 hash 绑定；观察候选不自动批准、不改词典或商品表。CSV/manifest 与报告行数、哈希一致。
- 修复否定守卫对常见中文等义表达和西语正向习语的漏识别：覆盖 `不留/不易掉/无纺布/无需/无痕/不再`，并处理 `Inborrable`、`innecesaria`、`se acabó` 等源语表达；排除 `sin esfuerzo/complicaciones`、疑问式修辞、“必不可少/不可或缺”，不把 `不粘/防止/不锈钢` 等正向属性当成通用否定。针对 9/28 同一 5,496 SKU 工作簿重审，`NEGATION_DROPPED` 从旧规则的 961 降至 657（少 304 条，约 31.6%）；`NEGATION_HALLUCINATED` 为 19，仍需作为审核发现复核，不宣称归零。中文表和西语源表均未修改。
- 将长描述压缩启发式接入 Stage 5 候选门禁：达到配置阈值的异常压缩产生 `DESCRIPTION_COMPRESSION_REVIEW`，候选不自动闭合、进入 P2 人工复核；策略和实现哈希进入 Stage 5 合同身份，未触发自动改写。新增模型候选回归。
- 详情单元格新增确定性整格规则解析：只有键、值和所需商品语境全部由版本化词典唯一覆盖时才跳过模型；任一部分未覆盖则整格回退既有模型/审核流程，禁止规则片段与模型猜译混拼。输出保留条目顺序、重复项与原分隔符，并纳入 Stage 5 合同身份。
- Stage 5 模型请求扩展同字段保护事实账本：在既有数字/技术 token 外增加单位、已识别认证和源中实际命中的已批准材质/属性/服饰/规格/数量/单位术语；请求记录和推理输入同步携带，Guard 仍是最终拒绝式门禁，词典未覆盖事实不因此被豁免。请求 ID 现绑定实际选中的字典哈希，词典变化后旧推理输出不能作为同一请求回放。
- 对应 Stage 5 inference task/contract 已升至 v3；旧任务结果不能绕过新请求身份和事实账本校验。
- 标准导出与 Template 1 的 manifest 和函数返回值均将“发布门禁通过”与“Gold 认证”拆开：`quality_status` 展示阻断/待复核/仅发布通过，`gold_status` 固定为 `NOT_CERTIFIED`、`gold_eligible=false`，并列出复核 finding 数，防止非阻断语义 finding 被顶层 PASS 掩盖。
- 同源术语有多个不同批准译法时，保护事实账本显式输出候选冲突而不任选；另加回归确认官方原始标签身份与普通业务徽标分别审核，避免仅凭“新品/可持续”业务状态掩盖官网标签原文丢失。
- 中文导出此前只渲染三类已知官网标签，可能静默丢弃未来新标签；现对所有非折扣 `raw_tags` token 保留原文，已知标签才附固定中文释义，并在正式 Export Gate 与全量审计中逐 SKU 对账。折扣百分比继续由结构化折扣字段表达。
- 修复模型缓存绕过 Owner Review 的路径：source hash 命中且 `quality_status=OK` 仅能形成待审候选，不能让商品/规格解析或正式字典导出变成 `READY/AUTO_READY`；未审核商品字典文本和未批准分类映射也不能直接生效。Apply review 清单与 coverage 报告显式保留名称/规格候选值，Stage 5 的已审核流程不变。
- 收紧知识生产解析器：source hash 匹配且 `validation_status=PASS` 的模型缓存仅显示为字段级 `REVIEW` 候选；商品字典、范围词典和术语词典仅在携带批准状态时成为 `READY`。裸字符串、待审和已拒绝值保留为 `REVIEW` 候选，手工覆盖仍优先。该解析器当前没有生产调用点，回归测试防止未来接入时重新打开审批旁路。
- 修复正式中文导出的审批证据断链：导出仓库现在读取 canonical `localization_field_provenance` 的审批人、审批时间和 freshness；Export Gate 要求有效审核人、带时区的可解析时间、`CURRENT` freshness 与已批准状态/source hash 同时成立，否则阻断。兼容表不被当作审批证据；provenance 同步改为 canonical 优先，并把非空整行审核元数据投影至字段，字段专属数据优先，避免更新兼容投影时清空审核元数据。回归覆盖导出读取、缺字段/无时区时间阻断、过期状态阻断和同步保留审核证据。此门禁可能使没有这些元数据的历史审核记录暂时无法正式发布；不能从状态标签推造审核人或时间。
- 为可验证的旧审批建立只读恢复：仅当当前中文字段值与 patch `new_value` 精确相等、patch source hash 等于当前产品 source hash、存在批准与应用事件且最新事件仍是 `PATCH_APPLIED` 时，Repository 才从不可变事件投影审批人/时间并标记当前 freshness；仅接受 `human:*`、`project-owner` 或经冻结 Stage 6 owner authorization 的主体，排除模型翻译授权标签；它不回写 SQLite、不把 PENDING 晋升为批准，也不覆盖与事件冲突的已有元数据。当前快照 68 个 `HUMAN_APPROVED` 字段匹配 source-bound patch，其中 45 条审批主体符合身份约定；已有 canonical 元数据完整字段可直接使用，30 条 Stage 6 字段可由严格匹配的已应用审批事件恢复。
- 同步 Gold finding 映射：未批准字段、审批 provenance 缺失、freshness 非 CURRENT、source hash 过期/不匹配均固定归入 L6，而不是落为 `UNCLASSIFIED`。
- 对 `runtime/db/action_tracker.db` 只读抽样统计（未写库）：当前 5,497 SKU 对应 32,982 个中文字段 provenance；其中 108 个字段状态属于已批准类状态，持久化层同时具备审批人、带时区审批时间且 freshness 属于 `CURRENT/FRESH` 的 18 个字段；另有 30 个 Stage 6 字段可从匹配的 source-bound approved/applied patch event 只读恢复。其余字段仍有大量未批准状态或缺乏可追溯证据。这个数量是当前库快照，不作为业务阈值；按现门禁当前生产中文正式导出仍会被阻断，需从真实审核记录恢复/补录，不得由状态标签补造。
- 对 `2026-09-28_034717` 的 5,497 条只读来源快照按生产详情解析器统计：41,120 个详情键值对、1,619 个规范化源键；当前版本化键规则覆盖 3,034 次出现（7.38%）。9 个封闭枚举键覆盖 889 次出现，其中 159 次有明确值规则（17.89%）。这证明 L4 仍有大量需词典化/Owner 审核的工作；以上是该批次快照指标，不作为长期业务阈值，也未写入本地化数据。
- 2026-09-30 认证 Guard 修复：认证 regex 改用 ASCII 标识边界，避免中文紧邻 FSC®/PEFC/BCI/Fairtrade 时漏识别；`公平贸易` 作为 Fairtrade 译法仅在同字段西语源明确出现 Fairtrade 时按上下文接受，不把通用 `Comercio justo` 误认成认证。使用原哈希不变的 9/28 两份 5,496 SKU 工作簿只读重审，认证遗漏/虚报从 125/2 降至 0/0，总 findings 从 27,183 降至 27,056。该变化只修正审查器识别，不修改工作簿内容。
- 2026-09-30 源单位冲突误报修复：`source_candidate_v2` 在跨字段比较前统一常见单位的单复数与明确缩写（如 `4 litros` 与 `4 litro`），不做 L/mL 等换算。相同 workbook 只读重审后，总 findings 从 27,056 降至 25,991；源 `NUMERIC_CONFLICT` 从 1,169 降至 831、`UNIT_CONFLICT` 从 961 降至 234；认证遗漏/虚报仍为 0/0。真实不同数值/单位继续阻断并保留复核证据，商品原文未改。
- 2026-09-30 L3 等价事实修复：数字 Guard 仅在同字段西语明确给出时识别 `1 plaza→单人`、`2 personas→双人` 和 `4 estaciones→四季`；单位 Guard 识别 `gsm↔克/平方米`，并对 `gramos por m²: 140 g` 这种单位写在源属性键的异常格式按上下文比较。未加 `季` 的全局数字识别，避免把“cada estación”误作显式数字；未加入未证实的单位换算。相同 5,496 SKU 审计现为 25,980 条 findings，`NUMERIC_DROPPED/HALLUCINATED=343/326`、`UNIT_DROPPED/HALLUCINATED=142/166`；源数字/单位冲突为 831/234，认证遗漏/虚报为 0/0。
- 2026-09-30 继续收窄词汇化数量：补入 `1 tamaño; vale para todos→均码`；中文 `N支` 仅在同字段西语已有同一显式数值时参与对齐，不将普通“一支/一杯”误认成明确计数。原先将 `支/杯` 加入通用数量词时全量误报增加，已撤销该泛化并回归验证。当前 9/28 同批审计 25,977 findings，`NUMERIC_DROPPED/HALLUCINATED=340/326`；例 SKU 2559035、2572745 的数量遗漏 finding 已消失。
- 2026-09-30 冲突比对语境与单位等值继续修正：支持 count×measure 多包装的件数、单件量及整包量候选；将件/单位别名归一；仅以显式同属性标签比较，过滤“包含包装”的尺寸与产品尺寸、晾晒总长与外形尺寸等不相干字段；同属性值集合只要存在一致的明示测量就不因额外合法尺寸报冲突。对 kg/g/mg、L/cl/mL、m/cm/mm/inch 做确定性单位归一，仅用于 QA 对比，原始源值不改写。相同哈希工作簿复审由 25,654 降至 25,099，再加本轮否定语义窄范围对齐后为 25,069；`NUMERIC_CONFLICT/UNIT_CONFLICT` 为 149/38，否定遗漏提示由 657 降至 627。工作簿未被写入。
- 2026-09-30 否定等义范围继续收窄：补充 `no se pega→不粘`、`no resbala→防滑`、`no amarillea→不易黄变`、`no se seca→不易干裂`、`sin granos→无谷物`、`sin conservantes añadidos→不添加防腐剂`；排除“no te costará nada”与“no pueden faltar”等非否定习语。双向回归确认源语 `opaco` 对应目标“不透明”时不误报，但西语 `transparente` 被译成“不透明”仍报否定新增。相同工作簿 `NEGATION_DROPPED/HALLUCINATED` 从 627/19 收敛为 602/19；finding 总数 25,069→25,047→25,044。
- 2026-09-30 数字重复提及误报修复：模型 Guard 现在允许同字段源重复提及同一数字、中文只陈述一次的情况，但要求源/目标不同数值集合完全一致且目标没有新增值；数字变更、遗漏不同数值仍拦截，单位和技术 token 守卫保持独立。9/28 同一哈希工作簿只读复审由 25,044 降至 24,940 条 findings，`NUMERIC_DROPPED` 由 340 降至 236，`NUMERIC_HALLUCINATED` 仍为 326。源表/中文表均未写入；报告在 `%TEMP%/action-localization-audit-duplicate-numeric-collapse-v1-20260930/`。
- 2026-09-30 Unicode 摄氏符等价修复：单位 Guard 将 `℃` 识别为 `°C/ºC`，避免官网写 `60 °C`、中文写 `60℃` 时报告单位遗漏。相同工作簿复审中 `UNIT_DROPPED` 从 142 降至 61；同时有 6 条仅中文显式写出摄氏度、而西语源只写 `grados` 或数值的记录新显现为 `UNIT_HALLUCINATED`，暂保留复核，不基于国家/语境擅自推断源单位。总体 findings 从 24,940 降至 24,865，工作簿哈希不变；报告在 `%TEMP%/action-localization-audit-duplicate-numeric-celsius-v2-20260930/`。
- 2026-09-30 否定 Guard 上下文与同义表达修复：排除 `no solo…sino también`、`nada menos que`、`sin importar` 和商品营销习语 `no te quedes sin asientos`；区分 `inodora`（无香）与 `inodoro`（马桶）；对“无缝”“防油/防尘”“难以触及”等目标表达要求源短语支持，避免泛化判定；补齐 `sin tener que ensuciarse las manos`、`no arranca`、`sin grasa`、无磷酸盐、袜口鞋内不可见等源绑定等值表达。9/28 同哈希工作簿复审 `NEGATION_DROPPED` 从 602 降到 466；`NEGATION_HALLUCINATED` 为 25（较此前 19 增加的 finding 保留复核，含源数字 0% 与无缝等属性语境，不宣称清零）；总 findings 从 24,865 降至 24,735。报告在 `%TEMP%/action-localization-audit-negation-final-v7-20260930/`，源/目标表只读未写入。
- 2026-09-30 否定同义识别续修：中文 Guard 补入 `再也不/不会再/从此不/永不/不必再/不需要再/无需再/不用再`，并将其作为明确否定（无西语否定时仍报新增）；`nunca más` 不接受弱化为“减少……”；`sin irritaciones` 的“减少/避免/防止……刺激”允许中间有具体语境修饰语；将 `nunca se tienen suficientes` 识别为正向习语，避免把“发夹永远不嫌多”当成缺失否定。新增正反向回归；完整测试 `python -m pytest -q` 为 658 passed。对同一只读 9/28 工作簿复审，`NEGATION_DROPPED` 由 466 降至 459（6 个既有译文被识别为等义表达，另 1 条正向习语不再误报），`NEGATION_HALLUCINATED` 仍为 25，其余 finding 数未变；总 findings 从 24,735 降至 24,728，SKU 仍 5,496/5,496，`full_gold_eligible=false`。复审报告在 `%TEMP%/action-localization-audit-negation-v9-20260930/`；西语源表和中文表 SHA-256 与 v7 相同，未改写。
- 当前验证：完整 `python -m pytest -q` 为 663 passed；v13 同哈希 9/28 工作簿审计为 24,653 条 findings，`full_gold_eligible=false`；官方标签不匹配 846、否定遗漏/疑似新增 459/25、无源无硫酸盐属性 1、数字遗漏/疑似新增 230/324、技术标记遗漏/疑似新增 192/64、单位遗漏/疑似新增 60/169，详情术语和描述事实边界仍有复核项。finding 包含待审信号且互相重叠，不等于错误 SKU 数。SKU 匹配 5,496/5,496；代码测试通过不代表自由文本语义全部已证明或 Gold 审查完成。
- 2026-09-30 官网标签残留定位与导出回归：审计中的 846 条 `OFFICIAL_LABEL_MISMATCH` 均为目标备注只写中文、未保留西语标签身份；按类型为 `Una opción más sostenible` 493、`Nuevo` 181、`Promoción semanal` 136、双标签 35、`Oferta de fin de semana` 1；另有 1 条 `OFFICIAL_LABEL_DROPPED`。这不是当前 `_zh_remarks` 的实现缺陷：当前导出器对已知标签附中文释义但保留完整西语原文，未知标签原样保留，折扣百分比单独结构化。新增 exporter→Release Gate 集成回归覆盖三类已知标签与折扣分离；`tests/test_release_gate.py tests/test_exporting.py` 为 38 passed。该证据只证明当前代码生成链能正确处理测试输入，不代表 9/28 旧工作簿已修复；按文件系统规定未改动 `F:\按日期整理` 中的表格，历史工作簿的 846+1 条标签 finding 仍待通过授权的新导出产物闭合。
- 2026-09-30 L3 技术 token/单位 Guard 续修：同源同值的型号、认证、接口或平台重复出现时，允许目标仅写一次，但要求所有不同 token 均保留、无目标新增；技术 token 末尾句点不再算 token 内容。将明确单位 `mm2/mm² ↔ 平方毫米`、`gr/m2 ↔ 克/平方米`、`kWh ↔ 千瓦时` 纳入单位词典，并确保平方指数不作为额外数量、mm2 解析不跨行吞并下一项数量。为毫米平方、能耗单位、重复 FSC/A4/接口、唯一技术事实遗漏及跨行 `125 mm\n2 modelos` 增加正反例；完整 `python -m pytest -q` 为 661 passed。对同哈希 9/28 工作簿全量复审（v11）相对 v9：总 findings 24,728→24,656；`TECH_TOKEN_DROPPED` 244→192，`TECH_TOKEN_HALLUCINATED` 71→64，`UNIT_DROPPED` 61→60，`UNIT_HALLUCINATED` 172→169，`NUMERIC_DROPPED` 236→232，详情 token 对齐复核 57→52；SKU 5,496/5,496，`full_gold_eligible=false`。SHA-256 仍是源 `dec4bfe2ee0009406de6e65b40c6648e771eca3f9cb80fe310e1dbf5fb0d4e79`、目标 `cd773ec10b928d9b0e02c1af72130d20307998a6225b84c81eaa2af8832a6852`，审计声明两表均未写入。报告在 `%TEMP%/action-localization-audit-tech-unit-v11-20260930/`。
- 2026-09-30 小数格式等值修复：数字 Guard 比较前移除不改变数值的尾随小数零，修正 `4,0 × 40 mm` 与 `4 × 40 mm` 被按文本精度差异误报的问题；有效精度（如 `4.05`）保持，千位分隔逻辑不变。新增数值计数器和完整字段 Guard 正反例。完整回归 `662 passed`。同一只读 9/28 工作簿 v12 相比 v11，发现数 24,656→24,652、`NUMERIC_DROPPED` 232→230、`NUMERIC_HALLUCINATED` 326→324；其余 findings 不变，SKU 匹配 5,496/5,496，`full_gold_eligible=false`。报告在 `%TEMP%/action-localization-audit-decimal-canonical-v12-20260930/`；两份源表 hash 不变、未写入。
- 2026-09-30 无源 free-from 属性拦截：真实 SKU `2546447` 的西语描述仅支持“纯素配方”，中文候选却加入“无硫酸盐基底”。新增窄范围同字段证据规则：中文无硫酸盐/SLS/SLES 声明必须由西语 `sin/libre de/no contiene sulfatos` 或 SLS/SLES 支持，否则 `UNSUPPORTED_NEGATIVE_ATTRIBUTE` 在 Stage 5 拒绝候选、全量 QA 记录、正式 Export Gate 阻断；不泛化处理所有“无/不”表达。跨三个路径及有/无源证据测试通过。完整回归 `663 passed`；同一只读 9/28 工作簿 v13 相比 v12 新增 1 条该 finding（SKU 2546447），其余计数不变，SKU 5,496/5,496、`full_gold_eligible=false`。两份工作簿未写入；报告在 `%TEMP%/action-localization-audit-sulfate-guard-v13-20260930/`。
- 2026-09-30 否定语义 v15 收窄：仅在同字段源出现 `Se acabaron...` 时，把中文“告别/对应问题消失”视为等义；对 `0% de alcohol` 分开处理保留数字的 `0%酒精` 与省略数字但写“不含酒精”的等值，避免把已保留的 0/百分比误扣或报漏。新增回归覆盖“告别坚硬表面/异味消失”、0% 数字保留、同字段其他数字必须精确等情况。完整 `python -m pytest -q` 为 664 passed。相同 9/28 工作簿 v15 相对 v14 findings 24,651→24,641，`NEGATION_DROPPED` 466→458、`NEGATION_HALLUCINATED` 16 不变、`NUMERIC_HALLUCINATED` 325→324、`UNIT_HALLUCINATED` 170→169；SKU 5,496/5,496，`full_gold_eligible=false`。工作簿源/目标哈希均与 v14 相同且只读未改；报告在 `%TEMP%/action-localization-audit-negation-equivalence-v15-20260930/`。finding 总量包含重叠的审计/复核信号，不等于错误 SKU 数，不能据此宣称完成 Gold。
- 2026-09-30 否定语义 v19 全量复核：继续以同字段证据将 `Se mantienen en su sitio → 不易滑落`、`plegable → 不用时可折叠`、自修复切割垫→不易留刀痕、`excepto → 不包括`、`sin aditivos artificiales → 无人工添加剂`、`sin vello → 无毛`、`no desprende olor → 无气味/无异味`、`hermético al aire y los olores → 防漏气/防串味`、`no se queme → 防焦`、`sin mangas → 无袖`、`antirrotura/anticarreras → 防勾丝`、`sin talón → 无后跟` 和官网异常值 `Inoloro: Sí → 无异味：是` 加入窄范围守卫；`sin tirantes` 不因此被认作“无袖”，无来源的“不粘/无缝/免维护”等仍保留为疑似新增。全套回归 `664 passed`。同一只读 9/28 工作簿 v19 相对 v14 的 findings 24,651→24,601，`NEGATION_DROPPED` 466→425、`NEGATION_HALLUCINATED` 16→9；数字/单位/技术 token 类计数未变，SKU 匹配 5,496/5,496，`full_gold_eligible=false`。工作簿哈希不变且未写入；报告在 `%TEMP%/action-localization-audit-negation-lexical-v19-20260930/`。余下 9 条疑似新增逐条抽查均有真实语义风险或字段范围不足，不予自动放行。
- 2026-09-30 否定 Guard v21 误报收敛：通过源码词形证据放行 `Inoloras → 无香`、官方 `Inoloro: Sí → 无味：是`，并排除中文“没有什么比……更好”修辞，保留 `Inodoro` 与 `Inoloro` 的词义边界。全量回归 `664 passed`。对相同只读 9/28 工作簿 v21 相比 v20，findings 24,536→24,529（减少 7），`NEGATION_HALLUCINATED` 18→11；`NEGATION_DROPPED` 维持 351，其他计数未变，SKU 5,496/5,496，`full_gold_eligible=false`。剩余 11 条新增否定信号抽查包含无源“无糖/无需养护/无缝设计/无香”与源描述不支持的“无需清洗/无阀门”等事实，继续阻断放行，不为降低计数而豁免。两份工作簿 SHA-256 与 v20 相同（源 `dec4bfe2ee0009406de6e65b40c6648e771eca3f9cb80fe310e1dbf5fb0d4e79`，中文 `cd773ec10b928d9b0e02c1af72130d20307998a6225b84c81eaa2af8832a6852`），审计只读未写。报告在 `%TEMP%/action-localization-audit-negation-coverage-v21-20260930/`。
- 2026-09-30 否定 Guard v26 结构词与语用收敛：补充无闭合/无特定场合、无硅油/无酸/无溶剂/无金属/无钢圈、无划痕/白痕、未加工/零热量/免加热、无紧束带等有同字段直接证据的表达；修复 `sin embargo`、`a lo mejor no`、`no te preocupes`、`sin duda` 等语用误报，并将 `防潮/防水` 限定到源语确实表达“不让湿气/水滴通过”的情形，避免把正向 `antigoteo` 误当新增否定。新增反例和等义例覆盖；完整回归 `664 passed`。对同一只读 9/28 工作簿，相对 v21 findings 24,529→24,472，`NEGATION_DROPPED` 351→294，`NEGATION_HALLUCINATED` 11→11；其余问题簇计数未变。SKU 匹配 5,496/5,496，但 `full_gold_eligible=false`；详情键覆盖仍仅 7.44%，审核队列 13,604 行。源/目标 SHA-256 仍为 `dec4bfe2ee0009406de6e65b40c6648e771eca3f9cb80fe310e1dbf5fb0d4e79` / `cd773ec10b928d9b0e02c1af72130d20307998a6225b84c81eaa2af8832a6852`，工作簿只读未写。报告在 `%TEMP%/action-localization-audit-negation-coverage-v26-20260930/`。
- 2026-09-30 否定 Guard v28 续修：对 `no tengas que preocuparte → 无需担心` 增加同字段支持且加无源安全保证反例；将“无忧/别担心”与显式“无需担心”分开识别；修复 `no te olvides de...` 指令、`no importa qué...`、`tampoco se escapará...` 修辞、“隔绝空气和水分”、`无泡`、`不滴落`、`不希望外露` 等映射。全量回归 `664 passed`。对同一只读 9/28 工作簿 v28 相对 v26 findings 24,472→24,453，`NEGATION_DROPPED` 294→275，`NEGATION_HALLUCINATED` 仍为 11；SKU 匹配 5,496/5,496，Gold 仍不通过。源/目标哈希未变，工作簿未写。报告在 `%TEMP%/action-localization-audit-negation-coverage-v28-20260930/`。
- 2026-09-30 否定 Guard v30 续修：加入 `nada se te resbalará de las manos → 不易从手中滑落`、`nada se pega → 不易粘锅`、避免不必要塑料袋使用→减少一次性塑料袋使用、`se adhiere menos a la piel → 不易粘附皮肤` 的源绑定匹配和无源新增反例；将 `en nada de tiempo`、`nada más llegar/entrar/salir` 排除为时间修辞而非事实否定。全量回归 `664 passed`。同一只读 9/28 工作簿 v30 相对 v28 findings 24,453→24,444，`NEGATION_DROPPED` 275→266，`NEGATION_HALLUCINATED` 11→11；其余 finding 计数不变。SKU 匹配 5,496/5,496，Gold 仍不通过；详情规则覆盖率 7.44%、详情审核队列 13,604 行。源/目标 SHA-256 未变，表格未写。报告在 `%TEMP%/action-localization-audit-negation-coverage-v30-20260930/`。
- 2026-09-30 否定 Guard v35 误报续修：中文否定等值词表补入“无老茧/无硬茧”和“无气泡”，修复 `sin durezas → 无老茧`、`sin burbujas → 无气泡` 被报作否定遗漏的问题；各增加源文支持/无源新增的正反回归。全套测试 `664 passed`。对同一只读 9/28 工作簿，从 v33 到 v35 `NEGATION_DROPPED` 264→262、总 finding 24,442→24,440，`NEGATION_HALLUCINATED` 11 不变，其余计数未变；SKU 匹配 5,496/5,496，`full_gold_eligible=false`。源/目标 SHA-256 未变，工作簿未写。v35 报告在 `%TEMP%/action-localization-audit-negation-coverage-v35-20260930/`。
- 2026-09-30 否定 Guard v37 续修：补齐 `sin cable(s) → 无线`（源绑定，且允许 `inalámbrico → 无线` 正向词义）、`sin polvo → 无尘`、`sin bolsa → 无尘袋`、`sin cinta de ajuste → 无调节带`、`sin que se deshagan → 不易松散`、`sin que se borre → 不易擦除`。审计发现将“无线”作为全局否定词会把 `inalámbrico` 等正向形容词错报，已改为源码绑定识别，并以有线反例保护。全套测试 `664 passed`。同一只读 9/28 工作簿 v37 相比 v35 `NEGATION_DROPPED` 262→243、`NEGATION_HALLUCINATED` 11→13，总 finding 24,440→24,423；v36 的 97 条过宽“无线”误报未保留。SKU 匹配 5,496/5,496，`full_gold_eligible=false`；其他问题簇仍在。源/目标哈希未变，工作簿未写。v37 报告在 `%TEMP%/action-localization-audit-negation-coverage-v37-20260930/`。
- 2026-09-30 否定 Guard v38 精修：`True Wireless Stereo → 真无线立体声` 可作为源文支持，不再被误报为目标新增否定；同字段无来源的“无线壶身”仍保留为 `NEGATION_HALLUCINATED`。新增术语正例；全套测试 `664 passed`。v38 相比 v37 总 findings 24,423→24,422、`NEGATION_HALLUCINATED` 13→12，`NEGATION_DROPPED` 243 不变；SKU 5,496/5,496、Gold 未通过，源/目标表哈希不变且只读未写。报告在 `%TEMP%/action-localization-audit-negation-coverage-v38-20260930/`。
- 2026-09-30 否定 Guard v40 续修：识别 `sin polvo → 不易扬尘`、`sin salpicar → 不飞溅`、眼线语境 `sin dejar manchas → 不易晕染`，并以源词绑定方式支持 `no gotea/antigoteo/antiderrames/a prueba de fugas/protección contra las fugas → 防漏`。审计确认无源“防漏”仍报警；对 `sin dejar marcas` 被弱化成“减少水痕”仍保留否定遗漏，不做等义放行。完整测试 `664 passed`。同一只读 9/28 工作簿 v40 相比 v38 总 findings 24,422→24,416，`NEGATION_DROPPED` 243→237、`NEGATION_HALLUCINATED` 12→12；SKU 5,496/5,496，Gold 仍未通过，源/目标文件哈希不变且工作簿未写。报告在 `%TEMP%/action-localization-audit-negation-coverage-v40-20260930/`。
- 2026-09-30 否定 Guard v41 续修：补齐 `sin bolsa → 无袋`、`no deja ningún residuo → 无残留`、`sin huellas dactilares → 无指纹`、`sin cables → 免插线`、`no deja burbujas → 不起泡` 等中文词形，并将 `no hay problema/没问题` 识别为修辞性正向表达。每项均有源支持/无源反例回归。完整测试 `664 passed`。同一只读 9/28 工作簿 v41 相比 v40 总 findings 24,416→24,408、`NEGATION_DROPPED` 237→229，`NEGATION_HALLUCINATED` 12 不变；SKU 5,496/5,496，`full_gold_eligible=false`，中文/西语源工作簿哈希不变且未写入。仍保留如袜子 `no sobresalen` 未译、灰尘清洁效果弱化等实际内容遗漏。报告在 `%TEMP%/action-localization-audit-negation-coverage-v41-20260930/`。
- 2026-09-30 否定 Guard v44 续修：把 `metales no ferrosos`（有色金属）、Femapure 名称中的 `No Worries` 排除为非否定；补足 `no debería faltar → 少不了`、`no hay nada más molesto que se bajen los calcetines → 袜子不易下滑`、`sin que falte la luz → 不遮挡光线`、`sin menta → 无薄荷`、`sin sustancias nocivas → 不使用有害物质`、`sin vitalidad → 缺乏活力` 等等值；对相框“照片突出且框不干扰”和手机挂绳“免放进口袋/腾出双手”加了上下文匹配。以明确相反源文做反例。完整测试 `664 passed`。同一只读 9/28 工作簿 v44 相比 v41 总 findings 24,408→24,373、`NEGATION_DROPPED` 229→194、`NEGATION_HALLUCINATED` 12→12；SKU 5,496/5,496，`full_gold_eligible=false`。`no desprende olores → 低气味` 的强度弱化仍保留 finding；哈希不变、工作簿未写。v44 报告在 `%TEMP%/action-localization-audit-negation-coverage-v44-20260930/`。
- 2026-09-30 否定 Guard v45–v48 续修：补入同字段等义 `no hace mucho ruido → 低噪音/声音较小`、`sin que nadie pueda verlo → 隐形墨水`、`no coja olores → 避免异味`、`no dañan el cabello → 不易损伤头发`、`sin resecarlas → 不使皮肤干燥`、`no se rompen al caer → 不易破损`、烘焙纸不被气流吹起、无 3.5 mm 插孔设备仅有 USB-C、`se mantienen en su lugar → 不易移位`、`sin dejar marca → 不易勒痕`、`no se caliente demasiado → 不易过热`、热卷发器型号及有来源支撑的钥匙/手机“减少丢失”表达；否定强度保持 source-bound，`nunca más perderás la maleta → 减少丢失` 仍会报弱化，来源未提丢失而目标新增“减少丢失”仍报无源否定。对反问、偏好和说明/促销语句按精确句式排除（包括不喜欢打孔、不知道选什么发型、不想频繁加水、酒店对比修辞和“别一口气吃完”）；普通产品否定继续有反例保护。完整回归 `664 passed`。同一只读 9/28 工作簿 v48 相比 v44 总 findings 24,373→24,354，`NEGATION_DROPPED` 194→175，`NEGATION_HALLUCINATED` 12 不变；SKU 5,496/5,496，`full_gold_eligible=false`。总 finding 含重叠复核信号，不等于确认错误数；剩余 175/12 条否定 finding 尚未清零，不宣称 Gold。两工作簿 SHA-256 仍为源 `dec4bfe2ee0009406de6e65b40c6648e771eca3f9cb80fe310e1dbf5fb0d4e79`、中文 `cd773ec10b928d9b0e02c1af72130d20307998a6225b84c81eaa2af8832a6852`，审计 `writes_source_workbook=false`、`writes_target_workbook=false`。v48 报告在 `%TEMP%/action-localization-audit-negation-coverage-v48-20260930/`。
- 本次未写入 Master、SQLite、正式字典或生产本地化数据；工作区仍有其他未提交改动，提交状态需单独核对。

当前生产安全收口提交为 `3826be0`，长期工具隔离提交为 `3ec134a`。
本文其余 Stage 4/5/6 数量是历史运行快照，不代表当前字典已获得新的 Owner/官网证据批准。

## 2026-09-12 Stage 4 Closure 最新状态

本节覆盖后文旧的 Stage 4/Qwen 快照；后文历史数量和状态仅保留作当时记录。

- Owner 签字包：`Qwen_Stage4_Stage5_OWNER_SIGNED_20260912.xlsx`，Stage 4 13 条 Gold 已确认。
- 2026-09-12 本次对话已明确确认两份 AI Owner Ready 包，登记为 owner-confirmed package（不改写原始工作簿）：Silver 485/485 覆盖完成，其中 472 条直接接受、12 条修订后接受、1 条 `3223907` 源冲突隔离；P0/P1 包 10 条 P0 + 1 条 P1 均已确认其根因处置。
- 源冲突 `3006792`、`3224748`：已按 owner 决定隔离，不进入 Gold。
- 旧 `NUMERIC_DROPPED`：已在当前 evaluator/acceptance 口径中降为历史信号；当前 Closure 的源数字丢失数为 0。
- 冻结 adapter 的 485 条独立测试已完成：JSON/schema/非空/一级类目均通过，但字段级硬事实仍有 10 条 P0、1 条 P1。
- 当前 recovery state：`READY_FOR_STAGE5_OFFLINE_SHADOW`；冻结 485 条在源绑定修复层复核后 P0/P1 均为 0，`FULL_STAGE4_RELEASE=true`。当前放行模式为 `MODEL_PLUS_SOURCE_BOUND_OWNER_RESOLVER`；训练授权和生产写入仍保持 `false`。
- 完整报告：`docs/STAGE_4_OWNER_SIGNED_FULL_EVAL_RECHECK_20260912.md`；人工审核包：
  `runtime/training/qwen3_8b/20260911/stage4_full_eval_owner_signed_p0_review_package_20260912.csv`。
- 独立源字段核验确认这 10 条 P0 均有直接西语源字段证据（不是单纯计数器表面误报）。
- 最终 Closure 总报告：`docs/STAGE_4_FINAL_CLOSURE_REPORT_20260912.md`；failure certification：
  `runtime/training/qwen3_8b/20260911/stage4_final_failure_certification.csv/json`。
- 初始 50 条 targeted remediation 队列保留为历史证据；v4 初选 200 条后，规则拦截 48 条。为满足“最终 200 条可审核候选”的目标，v5 补充池再审校并按五类各 40 条重组最终包，与既有冻结测试、训练/验证/测试和硬测试集交叉为 0：
  `runtime/training/qwen3_8b/20260911/stage4_targeted_remediation_review_queue_v4_200.csv/json`。
- 最终人工审核包为 200 条模型审校通过候选（品牌、数字遗漏、数字幻觉、技术 token、产品对象各 40 条），另保留两批原始规则拦截记录和 35 条合格备用：
  `runtime/training/qwen3_8b/20260911/stage4_remediation200_final_owner_review_20260912.xlsx`。
- 这 200 条已与当前 Master 的西语六字段逐条匹配，均为 candidate-only，`training_eligible=0`。受控校验入口 `scripts/validate_stage4_remediation_owner_review.py` 已切换到最终 200 条；后续仅签字完整、源字段未变化且 Guard 通过的行可输出为 Gold，脚本本身不训练、不写生产数据。
- 外部 AI 审核表标记为 `ACCEPT_AS_GOLD` 的 177 条已由项目所有者确认并登记；泄漏核验发现它们全部已出现在旧 train/validation/test 语料（131 train、29 validation、17 field-test），因此被标记为 `HUMAN_CONFIRMED_REMEDIATION_GOLD_LEAKAGE_BLOCKED`，`training_eligible_rows=0`，不得直接训练。原始候选和确认结果均保留，未写入 Master 或既有 split。
- 已修正 `scripts/build_stage4_targeted_remediation_queue.py`：生产模式现在扫描整个 `runtime/training/qwen3_8b` 历史根目录，统一排除所有 train/validation/test、冻结测试和既有候选语料；新增回归测试覆盖跨日期目录泄漏。
- 新收到的 AI Owner Ready 包已核验并经项目所有者在本次对话确认；原始工作簿中的签字列保持不变，确认记录见：
  `runtime/training/qwen3_8b/20260911/stage4_owner_confirmed_closure_20260912.json`。
- AI Owner Ready 复核报告：`runtime/training/qwen3_8b/20260911/stage4_ai_owner_ready_recheck_20260912.json`。
- 历史 Stage 4 P0 阻断已通过源绑定、字段级、Owner 已批准的修复层闭环；旧候选与严格审计文件仍保留为不可变历史证据，不覆盖旧模型产物。
- 本轮 Closure 只读审计未修改 Master、SQLite、字典或旧模型；最新回归测试为 `437 passed`。

## 2026-09-08 最新生产验收覆盖

以下是当前 PRIMARY 的最新状态，覆盖本文件后面的历史快照；后面的旧数量只作为当时验收记录：

- SQLite PRIMARY：`runtime/db/action_tracker.db`
- products：9,033；CURRENT：5,547；MISSING：26；OFFLINE：23；HISTORICAL：2,610；ABSENT：827
- 最新 committed head：`LOCALIZATION_PROVENANCE_REPAIR_20260908_localization_LOCALIZATION_PROVENANCE_REPAIR_20260908_bc9c848a_b35ca62aa1ef`
- 最新 Apply run：QA `PASS`、`dry_run=0`，5,547 个 CURRENT SKU 已写入字段级本地化和 canonical provenance
- SQLite integrity、foreign keys、presence states：`PASS`
- Master/known_skus/offline_skus 兼容投影：`export_sync=SUCCESS`
- 2026-09-08 中文/西语无图导出：各 5,547 条，SKU、价格、图片链接、商品链接逐条一致
- 当前分支的生产代码仍未合并 main，需按最小逻辑进行集成审查

当前保留两类非阻断告警：部分官网详情/二级类目源字段本身为空（导出备注已显式标记），
以及 51 个历史 SKU 没有可追溯的 `source_first_seen`、部分字典条目仍处于人工复核队列。
字典审计最新结果为 30 PASS / 2 WARN / 0 FAIL；西语导出 HTML、`null/undefined` 和双冒号残留均为 0。

## 1. 生产主链边界

Sitemap/Listing/补充入口 → Presence 冻结 → Lifecycle → QA → Snapshot/Staging →
QA 通过且非 dry-run 才能提交 SQLite PRIMARY，再生成兼容 Master/State。当前
`storage.mode=SQLITE_PRIMARY`，SQLite 是生产主链，Excel/CSV 是兼容投影。本轮没有修改
`monitor/listing.py`、`monitor/sitemap.py`、`monitor/sku_monitor.py`、`services/lifecycle.py`
或 Presence/Cloudflare/QA 核心语义。

详情运行策略：`run.max_detail_per_run=0` 表示不设单轮详情抓取上限；正数才会限制批次，
延迟项保留在 backlog，后续可通过父 run 的 `detail-retry` 受控补抓。详情页遇到 Cloudflare challenge 时最多等待 5 分钟，在第
2、4 分钟各刷新一次，仍未恢复则写入 `DETAIL_CHALLENGE_TIMEOUT` 并停止详情阶段；不绕过
验证，也不影响已经冻结的 Presence 事实。

## 2. 字典真实基线

| 数据集 | 当前正式/运行时行数 |
| --- | ---: |
| 商品字典 | 8,662 |
| 品牌字典 | 588（当前功能分支基线；main 旧基线 509） |
| 类目关系 | 186 |
| 术语字典 | 44（当前功能分支基线；main 旧基线 33） |
| 人工覆盖 | 197 |
| 模型缓存 | 823 |
| SOURCE_DAMAGED/SOURCE_POLLUTED | 130 |

运行时字典若缺少通过审计且与基线 hash 一致的证据，会自动回退到
`data/dictionary/`，不会把临时未审数据当成正式导出来源。

## 3. 真实覆盖率验收

针对正式 run `2026-08-26_130145` 的 CURRENT：

| 指标 | 数值 |
| --- | ---: |
| CURRENT SKU | 5,491 |
| AUTO_READY | 5,413 |
| AI-Free Rate | 98.5795% |
| REVIEW_REQUIRED | 71 |
| SOURCE_BLOCKED | 7 |
| 未确认品牌 | 3 |
| source_hash 变化 | 11 |
| SOURCE_DAMAGED / POLLUTED | 0 / 7 |
| 模型缓存使用 | 0（依赖率 0%） |

稳定目标 98% 已超过约 32 个 SKU（0.5795 个百分点），但 71 个审核 SKU 和 7 个源阻断不能
被忽略。报告在 `runtime/dictionary/reports/dictionary_coverage_2026-08-26.{json,csv}`。

## 4. 已实现的字典闭环

- `dictionary_resolver.py`：按字段输出值、来源、状态和 SKU 级
  `AUTO_READY/REVIEW_REQUIRED/SOURCE_BLOCKED`；人工覆盖优先，模型缓存仅在 source_hash
  匹配且质量为 OK 时可用；普通西语残留会进入审核。
- `dictionary-coverage`：只读统计 CURRENT，不修改 Master、State 或字典。
- `dictionary-apply --dry-run`：生成 `apply_preview.csv`、`field_diff.csv`、`review_required.csv`、
  `apply_manifest.json`；2026-08-26 真实 run 在严格未知品牌门禁后为 AUTO_READY 5,410、74 个审核 SKU、7 个源阻断。
- `dictionary-enrich`：只选择 NEW、source_hash 变化和 NEEDS_REVIEW，不访问官网、不调用模型。
- `review-queue build/decide`：稳定 review_id 去重，批准后按问题类型写入正确知识层；
  拒绝保留审计状态，已解决问题转 RESOLVED。
- `term-candidates`：从增量 SKU 统计术语、频次、覆盖 SKU、类目分布、上下文和来源日期；
  候选永不自动晋升，只有人工 APPROVED 才写入正式术语字典。

Dictionary Apply Gate 已实现，正式 Master 写入仍由 YAML 布尔值
`dictionary_apply.production_enabled=false` 明确关闭。Gate 会校验 QA/FULL_COMMIT、未过期审计、
Resolver、CURRENT 集合、运行时字典/基线逐文件 hash、并发 hash、字段白名单、唯一备份/锁、暂存验证、
原子替换和替换后回读；任何后续校验异常均恢复备份并记录 manifest 状态。当前 full dry-run 的不可变事实变化为 0。

## 5. Export 状态

基础 ES/ZH 无图导出、Template 1 三表无图/带图导出和独立历史 Presence 导出已在本地实现。
历史 Presence 使用 `1/0/UNKNOWN` 三态并附历史来源审计；图片资产同步、250×250 白底衍生图
和 ES/ZH 带图 Export 已实现，缺图不会删除 SKU。Export 只读取正式 QA/FULL_COMMIT 来源，
不重新访问官网。

## 6. SQLite 与图片实现状态

- SQLite V2：`CommitBundle`、`BEGIN IMMEDIATE`、外键/完整性检查、幂等 run、`base_commit_id`
  乐观门禁、`export_sync`、`sync-exports` 和 PRIMARY Read Repository 已实现并有 fixture 测试。
- 正式 runtime 数据库已完成 V2 baseline、parity 校验和 PRIMARY 切换；旧 V1 镜像与配置备份位于
  `runtime/backups/formal_cutover_20260830_120733`。三轮隔离 `SQLITE_SHADOW` 均为 parity 0，
  正式切换后的数据库校验也通过。
- 图片：`image-sync` 支持低并发、超时、指数退避、staging 原子 promotion、Manifest checkpoint、
  失败隔离和 SQLite PRIMARY 元数据镜像；当前配置不自动下载图片，Manifest 为空属于当前运行状态。

## 7. 测试与 CI

当前完整回归：`283 passed`。新增 Resolver、Coverage、Apply、Review Queue、Term Candidate、
Export 和 Template 1 测试已加入 CI-safe 白名单；CI 仅使用临时 fixture，不访问官网、不写生产
runtime、不发布字典基线。GitHub Actions 远端结果仍需以实际 workflow run 为准。

## 7.1 Knowledge Production V1（P3–P6）

已完成合同与离线安全基础：统一六字段 source hash、字段级 Resolver、增量翻译队列去重、
SOURCE_BLOCKED 排除、候选 Validator、字段级 Auto-Approval Shadow，以及 SQLite 的
`translation_resolution`、`translation_queue`、`translation_candidates`、
`translation_approval_audit` 表和 localization provenance 字段。配置中的
`knowledge.production_apply_enabled`、`translation.ai_enabled`、
`translation.auto_approval_enabled` 和 `scoped_dictionary.enabled` 均保持关闭。
生产 Apply、真实 AI provider、Scoped Dictionary 审批和 Auto-Approval 正式开启尚未执行。

## 8. 未完成/风险

1. Dictionary Apply 正式写 Master 尚未启用；Gate 已完整实现，但生产配置仍关闭。
2. 74 个 Review Required 和 7 个 Source Blocked 需要人工/可信西语证据处理；已分别生成
   `review_closure_report.csv` 与 `source_blocked_review.csv`，不使用中文反推西语。
3. 图片尚未进入自动 daily 主链；需要基于正式 CURRENT 单独运行 `image-sync`，再运行带图 Export。
   当前已完成 fixture/结构验收，真实全量图片性能基线仍待执行。
4. 工作区仍有此前 Template 1 与字典功能的待提交改动，提交时必须按功能拆分，不能混入
   runtime、报告、图片或密钥。

SQLite Production Source of Truth 的完整阶段计划（Contracts → Writer → Shadow → Read →
Cutover → PRIMARY）见 `docs/MASTER_DEVELOPMENT_PLAN.md`；当前完成了 Writer、接线、Read
Repository、三轮真实 Shadow 对账以及隔离副本的迁移/备份/恢复/Primary 演练，下一步是正式切换窗口评审。
Image Foundation 的 Phase 9–13（Contracts → Foundation → Slice → Full Sync → With-Images Export）
已完成 Contracts、Foundation、ES/ZH With-Images Export 和 Template 1 中文嵌图实现，真实全量同步/性能基线待执行。

## 2026-09-30 本地化否定 Guard v49–v52 进度

- 否定等义映射新增洗涤/刷牙次数、保持干燥、食品分隔、未分级、避免弄脏/油漆污渍、不弯腰/不弄湿双手、痘痘消退、光线不过刺眼、多余毛发、`sin interrupciones → 不间断`、`sin tirantes → 无肩带` 等源绑定表达；支持源文明确“去除/预防/中和异味”而中文以相应除味表述承接的情形。每类均有反例，避免无来源新增同类否定。
- 9/28 相同 5,496 SKU 工作簿只读复核：v49→v52 `NEGATION_DROPPED` 157→145，`NEGATION_HALLUCINATED` v52 为 12（v51 新显现 2 项后，经精准源表达匹配收敛），总 finding 24,336→24,324。当前完整回归 `664 passed`；`full_gold_eligible=false`，这表示并非所有翻译/审查问题已经修复。
- 仍有可定位的内容缺失或无源新增：包括袜口不露出被漏译、`sin tirantes` 被写成“无袖”、`sin dejar marcas` 被弱化成“减少水痕”、绝对“不再丢失”弱化成“减少丢失”，以及无来源的“不加盐/无缝/无需养护”等。Guard 只能留下证据并阻止误放，不能替代实际文案修复。
- 9/28 两表哈希保持源 `dec4bfe2ee0009406de6e65b40c6648e771eca3f9cb80fe310e1dbf5fb0d4e79`、中文 `cd773ec10b928d9b0e02c1af72130d20307998a6225b84c81eaa2af8832a6852`；报告明确 `writes_source_workbook=false`、`writes_target_workbook=false`，本轮未修改工作簿。v52 报告及逐条 `QA_LOG.csv` 位于 `%TEMP%/action-localization-audit-negation-coverage-v52-20260930/`。
- 2026-09-30 否定强度 v53：增加异味处理动作强度门禁，不再将 `elimina/neutraliza → 减少异味` 或 `previene/evita → 减少异味` 当作完整等义；源文为否定式 `no elimina ... malos olores` 而中文肯定“去除异味”时，另报 `NEGATION_HALLUCINATED`。对应“去除→去除”“预防→防止/避免”正例及逆向极性反例已加入回归。完整测试 `664 passed`。同一 9/28 只读审计 v52→v53 `NEGATION_DROPPED` 145→153（新显现 8 条强度弱化），`NEGATION_HALLUCINATED` 12 不变，总 finding 24,324→24,332；匹配仍为 5,496/5,496，`full_gold_eligible=false`。这些新增是对既有中文候选的 QA 发现，不是工作簿改写。两表哈希与 v52 相同，实时哈希回读一致，源/目标均未写入。v53 报告在 `%TEMP%/action-localization-audit-negation-coverage-v53-20260930/`。

## 2026-09-30 产品详情高频键词典 v5

- 详情键词典扩展为 `action-detail-terminology-v5`，新增一组可直接由西语字段含义确认的高频映射（颜色、数量、洗涤/熨烫说明、营养键、适用年龄、功率/口味/电压、含包装尺寸、线缆长度、电池/盖子、微波炉/洗碗机适用性等）；保留键值对顺序、重复项和值文本。`Cantidad → 数量` 不接受唯一观察到的“包装数量”别名，避免无源增加“包装”限定。
- 定向测试 `14 passed`，完整回归 `668 passed`。在同一 9/28 源/中文工作簿上的只读 v55 审计中，详情键出现覆盖由 v54 `8,331/39,981 (20.84%)` 升至 `21,193/39,981 (53.01%)`，新增规则覆盖 12,862 次；未映射源键由 1,477 降至 1,453。键值完整对覆盖仍仅 `29/12,004 (0.24%)`，说明大部分详情值尚未完成规则化/语义验收。
- v55 finding 共 25,566（较 v54 多 1,222），其中 1,278 条是新显现的规范键修复候选，覆盖 946 个 SKU；这是规则从“未知键”转为识别并提出标准键修复，不是错误数净减少，也没有直接修改工作簿。完整 Gold 仍为 `false`、审核范围仍是 `PARTIAL`。源/目标 SHA-256 仍为 `dec4bfe2ee0009406de6e65b40c6648e771eca3f9cb80fe310e1dbf5fb0d4e79` / `cd773ec10b928d9b0e02c1af72130d20307998a6225b84c81eaa2af8832a6852`，审计声明 `writes_source_workbook=false`、`writes_target_workbook=false`。审计在 `%TEMP%/action-localization-audit-detail-key-v55-20260930/`。
- 按持续修复授权，从 v55 `proposed` 生成独立中文修复副本 `F:/按日期整理/20260928/20260928Action商品全量_中文版_不带图_详情键v5修复版.xlsx`；原中文表和西语源表未覆盖。只对 946 个 SKU 的 K 列详情字段执行 1,278 个键名规范，逐字段值、条目数、顺序和分隔符均保持；全表逐格比对确认其余单元格不变，表格结构/筛选/样式保留。
- v56 对该修复副本重跑同口径全量审计：5,496/5,496 SKU 匹配，详情 `DETAIL_KEY_RULE` 从 1,278 降至 0；总 finding 25,566→24,281（减少 1,285，含少量关联译法变体计数变化）。`full_gold_eligible=false` 仍成立：L2 1,069、L3 1,202、L4 10,014、L5 11,765、L6 294 findings；这些包含 review-only/重叠复核信号，不能直接等同确定错误数。v56 源 SHA-256 未变，原中文表 SHA-256 仍为 `cd773ec10b928d9b0e02c1af72130d20307998a6225b84c81eaa2af8832a6852`；修复副本 SHA-256 为 `3786ba94232e009f5cdc861e01cb48f2e0a65d5ff58c6a01e0eb68715fc323c0`。报告在 `%TEMP%/action-localization-audit-detail-key-v56-20260930/`。

## 2026-09-30 续修副本与 Guard 误报边界

- 独立续修副本 `outputs/localization_repair_20260930/20260928Action商品全量_中文版_不带图_分类与描述续修版.xlsx` 对 15 个明确类目映射和 11 个源事实描述执行了 26 个单元格改动；源表、历史目录和原中文候选均未覆盖，5,496/5,496 SKU、表格结构、筛选和样式保持。
- `source_fact_repair` 与只读全量审计现读取已确认品牌短语，仅在西语标题精确命中时排除 `GS27`/`K2` 等 NO_BRAND 标题中应删除的品牌 token 误报；不改变中文标题、不放宽未确认型号检查。Stage 5 使用同一 SKU 级品牌范围。
- 数字 Guard 增加窄范围 `uno/una ... el/la otro/otra` 成对变体等义规则，允许中文把西语隐含的两项拆成“一条…一条…”；不放宽任意中文数量词。定向测试 103 项、完整回归 672 项通过。
- 续修副本最近一次完整审计仍为 `full_gold_eligible=false`；该结论不能由本轮误报边界修复单独改变。长时间复扫已停止，后续应使用分层/增量审计，不重复运行无界全量慢扫描。
- v60 独立副本在 v59 基础上按西语原文修复 7 个描述单元格：行李牌绝对结果、`sin tirantes`、3 条 `sin dejar marcas`、以及两条 `nunca más`/钱包丢失表述；总改动 33 个单元格，输出回读 33/33，SHA-256 为 `c2d0a559eaea892d82f153dc45f115f7e1050ae1ba93c0ba39a131696e8d43d1`。
- 数字 Guard 继续增加两类窄等义：`uno/una ... el/la otro/otra` 的成对变体，以及 `diez meses` 等西语文字数字+时间单位到中文阿拉伯数字。完整回归现为 `673 passed`；两类规则均不改变工作簿源值，也不放宽任意中文数量词。

## 2026-09-30 续修 v61–v66

- 基于同一西语源和 v58 中文输入，增量修复描述中的源事实遗漏、动作强度弱化、跨字段混入和无源新增；累计输出 51 个明确描述/类目单元格修复，另对 `Cubierta` 和 `Parte de la batería` 结构化字段按西语源逐 SKU 规范 71 个详情单元格。未改写源表、Master 或历史目录。
- v67 输出：`outputs/localization_repair_20260930/20260928Action商品全量_中文版_不带图_分类与描述续修版_v67.xlsx`；5,496 个唯一 SKU，表格维度和筛选/样式保持，Artifact Tool 回读验证 `122/122` 个变更单元格；SHA-256 为 `221e4c983f09a82c9554c24ecbee7dde3cb3e9d93f4cc4bd1cc11884b286e6c1`。
- `Cubierta` 只映射为 `封面：软封面` / `封面：硬封面`，`Parte de la batería` 只映射为 `电池化学体系`；独立的 `Tipo de encuadernación`（平装、螺旋装订等）不被重写。源中 76 个 `Cubierta`、37 个电池化学体系键逐条位置复核后均无未规范项。18 个本轮高风险描述在字段级 Guard 定向回归中均无硬错误。
- 本轮仍未宣称 Gold：旧全量报告包含大量 review-only/启发式信号，未重新启动无界长审查；需要后续采用分层、分批审计来验证全表，而不是把定向回归扩大解释为全量通过。

## 2026-09-30 续修 v68–v69：详情键/值确定性收口

- v68/v69 从固定 v58 输入重建，不叠加中间工作簿；在原有 51 个类目/描述修复、68 个 `Cubierta`、4 个 `Parte de la batería` 结构修复基础上，新增 20 个 `Relleno de página` 位置绑定规范和 14 个 `Polipropileno (pp)` 材质标记规范。共 157 个字段级修复操作，因同一 K 单元格存在多字段修复，合并为 139 个实际写入单元格。
- `Relleno de página` 只按西语源位置映射为 `内页格式`，值 `Con líneas/Blanco/Cuadrícula` 分别为 `横线/白色/方格`，保留同一字段中的其它值；`Material` 中的 `Polipropileno (pp)` 仅规范为 `聚丙烯（PP）`，不重排其它材质。
- v69 额外覆盖了官网无冒号变体 `Cubierta Cubierta dura`（SKU `3217509`），并与同一产品的内页字段修复合并写入。Artifact Tool 回读 `139/139`，5,496 个唯一 SKU、14 列、表格筛选和样式保持。v69 SHA-256：`cbb1f9b430cb7f773cd2fd97492c5c981807a2da98408e451f97522094618980`。
- 定向回归结果：`Relleno de página` 49/49、PP 材质 89/89、电池化学体系 37/37、封面 77/77、洗涤次数/墓地/鸟类相关项均无残留。该结果是规则覆盖回归，不等同于未经分层的全量 Gold 通过；`A color` 保留官网源字段差异，未盲目统一。

## 2026-09-30 续修 v70–v71：布尔/营养/器具字段键规范

- v70 将 `A color` 的 42 个源绑定条目统一为 `是否彩色：是/否`；v71 继续修复用途、适用宠物、储存建议、糖/盐、线缆、电池附带、盖子、洗碗机、微波炉和两条 ASCII 冒号材质格式，共 347 个字段级操作。
- 修复器现在同时识别中文分号、ASCII 分号和 `|` 分隔符；多字段同格先合并后单次写入。v71 共 546 个字段级操作，合并为 488 个实际单元格，Artifact Tool 回读 `488/488`。
- 定向回归：`A color` 42/42、适用宠物 154/154、用途 248/248、洗碗机 271/271、微波炉 170/170、营养/电池/盖子/线缆及此前详情规则均无残留。76 个源/中文详情条目数差异主要是已批准过滤 `Sustancia: Válido` 或官网无冒号/重复字段的源异常，不作为商品不存在或字段丢失处理。
- v71 输出：`outputs/localization_repair_20260930/20260928Action商品全量_中文版_不带图_分类与描述续修版_v71.xlsx`；5,496 个唯一 SKU、14 列、筛选/样式保持；SHA-256：`a33d5c99739b54e161831097d04e5203f8f2bbd43d98707d19c1a8a39bb14047`。仍不将该定向回归等同于 Gold 全量批准。

## 2026-09-30 续修 v72–v73：尺寸/餐具/数量与洗涤次数后缀

- v72 在 v71 规则基础上继续按西语源位置规范 `Tipo de plato → 餐具类型`（34 条）、含包装尺寸键（240 条）和 `Cantidad → 数量`（1,645 条源字段中的可对齐项），并保留源值、顺序和分隔符；v72 产生 570 个字段级操作，合并为 494 个实际单元格，回读 `494/494`。
- v73 回归发现 33 条 `Número de turnos de limpieza` 中仍有 26 条只改了键名、未补数值单位。修复器新增窄规则：纯数字值补为 `洗涤次数：N次`，不改动非数值或其它字段；v73 共 596 个字段级操作，合并为 520 个实际写入单元格，Artifact Tool 回读 `520/520`。
- v73 定向回归：洗涤次数 33/33、餐具类型 34/34、含包装尺寸 240/240、数量 1,645/1,645、是否彩色 41/41、封面 76/76、内页格式 48/48、电池化学体系 36/36 均无残留；5,496/5,496 SKU 与西语源主键一致。`装订方式` 等独立字段保留为合法译法，不与 `封面` 混写。
- v73 输出：`outputs/localization_repair_20260930/20260928Action商品全量_中文版_不带图_分类与描述续修版_v73.xlsx`；5,496 个唯一 SKU、14 列、筛选/样式保持；SHA-256：`dae4c5a6f4bcd68a9a4e0692a472439951b73793e40eb97d686b97f9633cced6`。完整回归 `673 passed`。这些是分层定向规则的可复核结果，不宣称未经分层的 Gold 全量批准；源异常仍按 `SOURCE_ANOMALY` 规则单独追溯。

## 2026-09-30 续修 v74：残留描述与标题品牌规范

- 对照 `20260929_Action中文研究版_Gold最终审查_残留问题清单.xlsx`，v74 继续修复 4 条描述残留：Testaankoop“最佳购买”事实、砂纸数量/型号/FSC、汽车抛光套装 5 件组成等，均按西语原文重建，不添加源外事实。
- 同时只从 7 个标题中删除已确认商业品牌，保留研究事实：`HDMI`、`MMMAX`、`Mmmax` 系列、`维纳斯`、`Pro-陶瓷` 和 `10合1` 等型号/系列/技术信息。
- v74 共 607 个字段级操作，合并为 531 个实际写入单元格，Artifact Tool 回读 `531/531`。最新残留清单中的详情、描述、标题三类旧值均不再原样存在（0 条未变化）。
- v74 输出：`outputs/localization_repair_20260930/20260928Action商品全量_中文版_不带图_分类与描述续修版_v74.xlsx`；5,496 个唯一 SKU、14 列、筛选/样式保持；SHA-256：`ef70d3d8fffe51f425b0e6b319d20f9133e2b567d602cab5cf856d8affb1d7e0`。定向结构回归仍为 0 残留，完整回归 `673 passed`。官网异常字段继续按 `SOURCE_ANOMALY` 规则保留可追溯处理；v74 同步生成 71 行独立账本（46 条 `Sustancia: Válido`、25 条 `Incluye oído`），CSV/manifest 哈希绑定且 `qa_id` 唯一，不把异常值猜改成正常值。
- v75 在 v74 基础上修复 1 条因源字段重复导致未对齐的详情键：SKU `2576384` 的 `Sal: 0.28 g` 规范为 `盐分：0.28克`，不改变数值和相邻营养字段。v75 共 608 个字段级操作，合并为 532 个实际写入单元格，回读 `532/532`；定向规则回归（洗涤次数、餐具类型、尺寸、数量、A color、封面、内页格式、电池化学体系、盐分）均为 0 残留，5,496/5,496 SKU 对齐。
- v75 输出：`outputs/localization_repair_20260930/20260928Action商品全量_中文版_不带图_分类与描述续修版_v75.xlsx`；SHA-256：`6b7ab2acb4c96a5be4acaaad3b9ad6890e810258ab0814cea2807c77b9b50964`。完整回归 `673 passed`。对应 97 行 `SOURCE_ANOMALY` CSV/manifest 已绑定到 v75：46 条 `Sustancia: Válido`、25 条 `Incluye oído`、26 条官网错误度量字段名，分别记录过滤或中文键规范化动作。
- v76 继续收口结构化详情键：针对源/目标条目数不一致导致遗漏的洗碗机、微波炉旧别名，追加 43 个源绑定键规范化；所有对应字段现在统一为 `是否可用洗碗机清洗` / `是否适用于微波炉`，原值和顺序保持。v76 共 651 个字段级操作，合并为 556 个实际写入单元格，回读 `556/556`。
- v76 输出：`outputs/localization_repair_20260930/20260928Action商品全量_中文版_不带图_分类与描述续修版_v76.xlsx`；5,496 个 SKU 对齐，洗涤次数、餐具类型、尺寸、数量、A color、封面、内页格式、电池化学体系、盐分、洗碗机和微波炉键回归均为 0 残留；SHA-256：`746441e1a8e1b1bc759806c7d827e03313b46c774e35c8b558f2064c9a74a4eb`。97 行 `SOURCE_ANOMALY` 账本复制到 v76 命名。
- v77/v78 继续处理源字段无冒号造成的错位：补齐 `De los cuales azúcares → 其中糖` 和 `Pilas incluidas → 是否附带电池` 的别名规范；v78 最终为 654 个字段级操作、558 个实际单元格，回读 `558/558`。规范键扫描仅剩 4 个有上下文的专用材质键和 1 个允许的 `货号` 别名，没有可安全自动统一的残留。
- v78 输出：`outputs/localization_repair_20260930/20260928Action商品全量_中文版_不带图_分类与描述续修版_v78.xlsx`；5,496 个 SKU 对齐；SHA-256：`f3a2fddded53579bbab69ec6f1adbec43eb81074e58a001530e1bb58f63a0484`。97 行 `SOURCE_ANOMALY` 账本已复制到 v78 命名；完整回归 `673 passed`。

## 2026-09-30 续修 v79：结构化残留与标题技术事实

- v79 从固定中文基线重建，新增 25 个高置信度事实修复：2 个产品详情残留（SKU `2569123` 的 `洗涤次数：26 次` 统一为 `洗涤次数：26次`；SKU `2580491` 的 `适用宠物：鸟` 统一为 `适用宠物：鸟类`），以及 23 个标题中的型号、平台、接口、系列或明确功能事实（如 Blue3、Fusion5、MagSafe、Nintendo Switch、USB-C、AA/AAA、GaN³、Sensor3 等）。仅删除/避免商业品牌噪音，不删除这些研究事实。
- v79 共 679 个字段级操作，因同一详情单元格多次合并后为 582 个实际写入单元格；Artifact Tool 回读验证 `582/582`。5,496 个唯一 SKU、14 列、表格筛选和样式保持；固定基线 SHA-256 仍为 `3958e39ee84469bc4600da1bf07b2ada11ad2cc7c3b6b4f17b3fc8b983a3e554`。
- v79 输出：`outputs/localization_repair_20260930/20260928Action商品全量_中文版_不带图_分类与描述续修版_v79.xlsx`；SHA-256：`609d6ada7ecd2eba546139769c722fa2ac749bb6e79f4673f036dac768eb8ec3`。97 行 `SOURCE_ANOMALY` 账本复制到 v79 命名，源西语内容未修改。完整回归 `673 passed in 16.17s`。
- 结论：这批修复是有效的，且已通过字段级回读和自动回归；但此前只读全量审计仍含大量 review-only/启发式信号，`full_gold_eligible` 不能因此自动改为 `true`，所以当前版本应称为“高置信度续修版”，不是未经分层复核的最终 Gold。

## 2026-09-30 续修 v80：标题字段契约回收

- v80 从固定中文基线重建，修复 11 个标题字段契约问题：移除从规格/描述跨字段带入的 `2合1`、`3合1`、`5合1`；补回标题源自身明确的贴纸、`Fresh Impact`、`All-in-One`、无限灯效、`Extra` 和 `Cookies & Cream`。
- v80 产生 690 个字段级操作，合并为 593 个实际写入单元格，Artifact Tool 回读验证通过；5,496 个 SKU 与 14 列结构保持。输出 SHA-256：`f12178d2fe9fd8ad38cf8d979640940d964dc127cb5be8c2d07e14d610829785`。
- 相对 v79，全量只读审计 findings `23,111 → 23,099`；`NUMERIC_HALLUCINATED 319→313`、`TECH_TOKEN_HALLUCINATED 64→59`、`TECH_TOKEN_DROPPED 162→161`。`full_gold_eligible` 仍为 `false`，其余大项以 review-only/启发式队列为主。完整回归 `673 passed`。

## 2026-09-30 续修 v81：跨字段数量/技术词回收

- v81 继续移除 8 个高置信度跨字段问题：`Max` 标题不再凭描述添加“无糖”；四层置物架、沙拉餐具不再从详情带入数量；圣诞灯串、普通灯串不再无源添加 LED；描述中的 LR03、AA/AAA 不再从详情或其他字段带入；同时保留源描述中的微型电池含义。
- v81 产生 698 个字段级操作，合并为 601 个实际写入单元格；相对 v80 实际新增 8 个单元格改动。输出 SHA-256：`3a577c1415588050706396a02ec179dec609940be225b1be9b5c521967e086e6`。97 行 `SOURCE_ANOMALY` 账本保持哈希一致，源西语表未修改。
- v81 全量只读审计 findings `23,099 → 23,089`；`NEGATION_HALLUCINATED 1→0`、`NUMERIC_HALLUCINATED 313→311`、`TECH_TOKEN_HALLUCINATED 59→54`。5,496/5,496 SKU 对齐，完整回归 `673 passed in 18.48s`。
- 当前结论仍不是最终 Gold：审计仍有 23,089 条混合 review 信号，其中绝大多数是词典变体、跨字段复核和未自动应用的规则队列；不能把这些启发式条目直接当成确定错误，也不能据此宣称全量已完全通过。

## 2026-09-30 续修 v82–v85：高置信度详情/规格/描述事实边界

- v82 在 v81 基础上按字段源边界修复 4 个详情问题（3 条“绷带条”、1 条人工色素否定方向）、4 个规格问题（移除无源单位/物件扩写）和 1 个标题跨字段 LED 误带入；共 707 个字段级操作，合并为 609 个实际写入单元格，回读验证 `609/609`。v82 SHA-256：`a85e7180fe33fff05f129c65fec8be7e6e73e5d3ef81c59f79ae70dfce41fbc8`；只读全量审计 findings `23,089 → 23,083`，5,496/5,496 SKU 对齐。
- v83 增加 16 条 description/details 高置信度修复：删除 3222971 的无源可持续/GRS 段并保留 LAB31，删除 3222947 的跨字段端口功率/PD/保护声称，移除 3223586 的详情数量带入，补回 2 条 A4 及 Lab31/Fusion5/GaN2/All-in-One Series 3/The Grinch/Sensor3/All-in-1/Wi‑Fi 等源描述事实；共 723 个字段级操作，合并为 625 个实际写入单元格。v83 SHA-256：`2dc01de38b8ae807fdf7940e9ebb38cbce9aad7a6581a17c7a0eb4af2f3591d5`。
- v84 继续补回 21 条源描述明确的 `LED` 技术事实；共 744 个字段级操作，合并为 646 个实际写入单元格，回读 `646/646`。v84 SHA-256：`a2d155d96e80e1d9c822bfec14b58922076a7ce0134956f46c09050c2e6d506c`；全量审计 findings `23,083 → 23,035`，`TECH_TOKEN_DROPPED 161 → 129`、`NUMERIC_DROPPED 208 → 199`、`NUMERIC_HALLUCINATED 310 → 308`，5,496/5,496 SKU 对齐。
- v85 对 3225509 补回 `XL`，3227277 补回 `Somat`，3224396 补回 `LSC Smart Connect`；保持 646 个实际写入单元格，回读通过。v85 SHA-256：`62490fc82e7e320cc914ac2e5bc83913be9d11c5258c0f8adcf820eb5081947e`。全量审计 findings `23,035`，`TECH_TOKEN_DROPPED 129 → 128`，5,496/5,496 SKU 对齐；完整回归仍为 `673 passed`。
- v82/v84/v85 均未覆盖源西语表、Master 或正式生产文件；97 行 `SOURCE_ANOMALY` 账本继续单独保存。`full_gold_eligible=false` 仍成立：剩余数字/单位/词典变体和详情对齐条目包含大量 review-only、官网异常和词典覆盖队列，不能直接等同确定错误；当前输出仍是“高置信度续修版”，不是最终 Gold。
