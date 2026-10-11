# 3,196 SKU localization continuation

The execution scope is frozen from PRIMARY at
`2026-10-10_desc_details_repair_20261010T180308518582Z_c0f6b8cee2de`.
Its 3,196 unique SKUs contain 8,756 outstanding fields: 4,633 blank targets
and 4,123 nonblank targets requiring approval, freshness or source binding
review. These are completion/review counts, not approved Apply counts.

Runtime evidence lives at
`F:/ActionSKUTracker/runtime/reports/localization_pending_scope_20261011/execution/`.
`scope.jsonl` records each field's current value, Spanish source, full source
context, provenance, issue reasons and historical source evidence. Its SHA-256
is bound by `baseline.json`. Unknown source evidence is kept for source audit;
it is never treated as a verified official empty field.

Work uses the existing historical Translation Registry / QA / owner delegation
/ immutable patch / Apply path. It preserves the historical-only boundary of
`HISTORICAL_OWNER_DELEGATION_V1`; CURRENT fields cannot be passed to that grant.
Review, translation and approved-but-unapplied counts remain separate. Candidate
creation never counts as a production Apply. Metadata rebindings are reported
separately from text corrections and missing-field backfills.

## Compact measurement regression

Real specifications for SKUs 3215347, 3224104, 2561124 and 2581866 preserve
their counts, measurements and units in `8×70ml`, `12×50ml`, `30×17cm` and
`38×38cm`. The unit scanner nevertheless rejected them because its broad
Latin-1 identifier boundary included the multiplication sign U+00D7.

The boundary now excludes multiplication/division separators while retaining
the Latin identifier and digit guards. It does not change source facts,
generation rules, dictionaries, protected models or approval policy. The real
source/target pairs and source artifact hashes are in the regression fixture;
tests also reject dropped/changed units and numbers embedded in identifiers.
An independent source comparison supplies KEEP decisions; Guard PASS alone
never supplies semantic approval.

The 30-field specification pilot retains four quantity/sheet-page source
conflicts. Decibel token-spacing/localization and PPP/DPI equivalence remain
separate QA review issues; this fix does not waive them.

## Cubic flow regression

The source-bound specification for pool pump 3004507 is
`2,3 m3/hora | 12 voltios`. Its retained candidate
`2.3 立方米/小时 | 12 伏特` is independently faithful, but the old Guard
counted the ASCII exponent as another quantity and classified cubic metres
as ordinary metres. This is a reproducible QA false rejection, not a reason
to waive source or approval requirements.

The Guard now recognizes cubic units and equivalent Chinese unit names,
removes standalone measurement exponents from numeric quantity comparison,
and preserves the hourly cubic-flow denominator. Model suffix digits remain
literal. New cubic measurements require their own source field; a quantity
in the description cannot authorize adding flow to the specification.
The fixture binds the real source, retained candidate and source artifact
hash. Regression negatives cover area/length substitutions, dropped/changed
rate denominators, numeric changes and cross-field fact imports.

This change does not generate translations, publish dictionaries, change
the source hash contract, deploy the daily runner or approve high-risk name
corrections. Only the existing native Registry / QA / Approval / immutable
Apply flow can publish reviewed targets.

Validation: the real pool-pump case and two numeric/own-field assertions fail
before the change. After the change, the 18 cubic/compact-unit regressions
pass and the full local suite passes 1,325 tests. The source-bound pool-pump
candidate passes preflight QA after the repair; preflight is not an approval
or an actual PRIMARY Apply.

## Source-faithful brand/IP preservation

The native resolver marked typed brand/IP terms as display-omittable in
every field and deleted them after provider translation. Deterministic
planning also deleted them from specifications and prose. Both paths now
apply the no-brand policy to names only, as required by the shared V2
review contract. Other fields retain their own source-supported tokens;
title tokens cannot authorize adding them to a description.

The real pencil-case SKU 3217243 fixture records its full own-field source,
source hashes and rehashed historical CSV evidence. Its regression output
tests token preservation only; it is not a gold translation or approval.
Isolated tests cover the deterministic specification path, provider details,
continued no-brand names and cross-field import prevention. All 84 focused
resolver tests and the 1,330-test local suite pass. The fixture-bound test
module also passes all five tests after evidence binding.

Four additional descriptions were generated through the existing native
Qwen-MT adapter and finite candidate pipeline. Full request/response hashes,
request IDs, usage, prior candidates and source evidence are retained in
runtime reports. Resume reused all four with zero provider calls. Three
remain QA-blocked (including numeric representation and contextual goma
findings); one retains HIGH risk and requires Owner approval. Actual Apply
from these four is zero. They do not reduce the missing-field statistics.
This branch remains local; the change does not deploy the daily runner,
publish terminology or expand the finite approval contract.

## Contextual erasers, scaled counts and bounded technical tokens

Real pencil-case SKU 3217243 lists an isolated `goma` alongside pencils and
a ruler/sharpener. The semantic parser now identifies that own-field list
item as an eraser, without inferring rubber composition. It preserves a
separate `Material: Goma` assertion and does not borrow a title's stationery
context for an ambiguous description. Both QA and provider terminology use
this scoped grammar rule; the formal dictionary remains unchanged. QA
rejects rubber bands, omitted erasers and negated eraser mentions.

Real cable SKU 3009538 uses `50 000 veces`; the generated candidate uses
`5万次`. Numeric QA now compares exact 万-scaled values before explicit count
classifiers using decimal arithmetic. Changed values, model suffixes, bare
fragments and counts borrowed from another field remain protected. This
comparison never rewrites source/target strings or approves durability claims.

Real remote-control car SKU 3212557 exposed an extra substring count of LED
inside Spanish `teledirigido`. Technical multiplicity now counts bounded
Latin identifiers, permits adjoining Chinese labels and retains accent
boundaries. Tests reject omitted/extra actual LED occurrences and distinguish
OLED from LED. A mechanically passing candidate still requires independent
meaning review; a new candidate's 双向操控 does not establish the meaning of
the source's `acción bilateral`.

All fixtures include real own-field sources and rehashed historical evidence;
isolated negatives are explicitly test scenarios, not invented official facts.
The 48 contextual/numeric regressions and 124 combined protected-token tests
pass. The intermediate full suite passes 1,350 tests; final full-suite results
after the bounded-token change are recorded with its Git commit.
The final full local suite passes 1,359 tests after all three repairs.

## Structured materials and Spanish articles in technical QA

Real detail SKU 3003940 uses `Material: Hierro, Bambú`; its retained Chinese
material value is `铁, 竹`. Short `竹`/`木` equivalents are now recognized only
as complete list atoms inside a single explicit material pair in details.
Source and target duplicate material keys cannot use this exception. Bamboo
fibre, qualified wood, wood appearance, negation, a pattern key and another
source field cannot authorize a bare material atom. SKU 3217432 retains its
untranslated/invalid-key blockers after the wood false positive is removed.

SKU 3200285 exposed the uppercase Spanish article `DEL` inside a phone-holder
type label. QA excludes the article only in explicit own-field labels for
phone holders, article numbers and handle material. The exclusion affects
the technical scanner view, never source text, hashes or provider input.
Keyboard/model DEL identifiers, including an actual key beside an article
in the same field, remain protected. The original protection tokenizer is
unchanged.

The evidence comparison executes the former 53e2590 QA and the revised QA
against the same three real sources with the repository knowledge loaded.
3003940 and 3200285 change from fact-QA FAIL to PASS; 3217432 remains FAIL.
These candidates retain their earlier source-conflict/HIGH review holds;
fact-QA PASS does not supply semantic approval or authorize Apply. The new
scanner regressions are CI_SAFE, read repository fixtures/dictionary only,
and never call a provider or write a production database. The intermediate
full local suite passes 1,382 tests.

## Revalidation inside the native Apply lock

The finite runner now compares the review's complete six-field source
context after acquiring the shared production lock. A competing writer
could otherwise change another source field between outer preflight and
lock acquisition, invalidating the semantic review while leaving its own
field text identical. Such rows stay `SOURCE_CONTEXT_CHANGED_REVIEW_REQUIRED`
before Registry revision/approval/patch creation. Missing context stays
`SOURCE_CONTEXT_REVIEW_REQUIRED`. Legacy canonical context keys remain
accepted, and unavailable None evidence is distinct from empty text.
This check supplements the existing own-source and Chinese target baseline
guards; it does not alter daily-run field-level caching or the approval scope.

The former batch05 worker ended at its configured wait deadline with zero
Apply. Its reports are retained. A subsequent combined batch can use the
same reviewed 36 specs and 38 newly reviewed names only after new-code QA,
full regression and live context revalidation. Candidates and prepared
manifests remain separate from accepted PRIMARY Apply counts.
The final local regression suite passes 1,385 tests after the locked context
check. Focused delegated-approval, scanner and daily compatibility checks
pass 55 tests. These results confirm local code regressions; the pending
74-field batch still requires its own native Apply and integrity evidence.
# 2026-10-11 后续验收及类目审批修正

描述 11 字段批已通过实际 Apply、受保护数据完整性检查、Master Sync 和同批 Resume；11 个字段中 5 个译文修正、6 个原值审批与来源绑定恢复，Resume 重复 Apply 为 0。本轮累计验收 279 字段 / 258 SKU：3 个缺失补译、17 个已有中文修正、259 个原值绑定恢复。此统计仅覆盖当前 3196-SKU 继续处理阶段，不代替此前完整历史 Apply 账本。

又完成 50 个描述的逐字段独立审核：21 个 KEEP、14 个普通修正通过 QA，15 个保留待复核；35 个候选尚未计为入库。原 3196 个 SKU 的全六字段库存为 19,176 字段，8756 个初始 pending 字段只是队列基线，不能代替全量语义审查范围。新库存报告覆盖当前中文、来源及审核证据；此前已批准值仍须核对旧审核证据后复用，不能把非空或 Guard PASS 当作语义完成。

真实历史 SKU 3218152 的 Jardín / Herramientas 字典行仅为 CAT1_CONFIRMED，原加载器却加载其二级“工具”。运行字典共复现 13 个同类误加载，修正后为 0：KnowledgeLoader 按原有知识信任状态加载，CAT1_CONFIRMED 只允许一级映射；Planner 的上下文术语不能在受治理知识快照中绕过二级审批；既有正式历史 runner 在 Registry / delegated approval 前校验已确认父子映射及目标一致性。没有修改正式字典、PRIMARY 西语或 schema，也未另建翻译流程。

新增真实来源 fixture 与临时字典测试验证父类确认、批准映射、错误父类、错误目标、未批准状态、冲突字典及 daily Planner；46 项针对性回归通过，完整回归 1,399 项通过（102.68 秒）。35 个描述候选按修改后的模块重新 QA 全部通过。改动仅在独立分支本地实现，未 push、未部署 daily 主线。完整在线 daily-run 仍未验证。

运行证据位于原 execution 目录：batch07_descriptions11_apply / integrity_verification.json、batch07_descriptions11_resume / pilot_review_apply.json、existing_descriptions50_independent_reviews.json、category_trust_loader_before_fix.json / after_fix.json、goal3196_full_six_fields_inventory_summary.json。运行产物不进入 Git。

## 描述35验收及品名来源消歧

描述35批实际 Apply 35（14 个已有文本修正、21 个原值绑定恢复），Master Sync 和保护数据完整性检查通过。首次 Resume 因另一个生产任务在锁间隙取得共享锁而终止，没有重复写入。使用原提交 `09524e3` 的 Git archive 固定代码，仅补做 Resume：35 个 NO_OP、重复 Apply 0、ALREADY_SYNCED。原 RESUME_FAILED 证据保留，补做证据另存 `batch08_descriptions35_resume_recovery_execution_status.json`，没有重启整批或干预其他生产任务。

本继续处理阶段累计验收 314 字段 / 288 SKU：3 个缺失补译、31 个已有中文修正、280 个原值绑定恢复。另一个任务后续提交的 browser80 head 已同步，本阶段实际 Apply 统计不包含其工作。当前原3196-SKU目标队列为3186 SKU /8434字段：缺中文4624字段、中文或来源绑定复核3810字段，已审核普通候选等待Apply为0。全六字段语义审查仍未完成；已独立审核的初始pending字段638个，不替代19,176字段全量范围或此前旧审核证据的重新验证。

新审查25个缺失品名，7个明确来源支持的高风险修正方案、18个来源歧义或旧Gate阻断均保留待复核。真实SKU3223681品名Gomas3D Minecraft的描述首行明确3gomas de borrar，旧模块却生成READY橡皮筋，正确3D橡皮擦反被QA要求橡皮筋。现修正为：只有同SKU明确、正向的描述主体及办公类目才消歧标题Gomas；办公类目本身、否定和无关提及不足以推断橡皮擦。通用手作橡皮筋种子不用于不明确的办公标题；单独橡胶材质及明确弹性圈保留。名称主体必须来自本名称事实或绑定本名称的明确消歧，不能拿描述里的配件或否定对象做标题主体。现有已审核名称继续复用。

2D/3D使用既有受保护TECH token流程，不能遗漏、改写或把其中数字用作额外包装数量；名称保留3D前缀。只有A4、USB-C或3D标记而没有商品主体的候选为REVIEW_REQUIRED。新增真实fixture、反例与provider请求断言加入CI_SAFE；63项针对性检查和完整1,412项回归通过。没有发布正式字典、部署daily或宣称线上全日流程通过。

一次真实Qwen调用生成`3D橡皮擦《我的世界》`，主体及技术标记正确，但IP展示违反品名策略，Codex独立修正方案为`3D橡皮擦`。保留旧HIGH商品身份修改及来源lineage，等待Owner审批；本候选Apply0。实际同批生成Resume新网络调用0、复用1响应。总真实新服务调用6，原始候选机械QA5PASS/1FAIL，批准0/入库0；机械PASS不代替语义与Owner审批。原候选IP政策问题未通过本修正自动批准，也不删除描述/详情中的IP。


## 详情批次验收、全范围证据复用及容量单位 QA

详情20、详情19两批经正式 Registry/QA/有限自主审批/Immutable Patch/Apply 完成；各自 Master Sync SUCCESS，完整性检查 PASS，原值分别保留20、19字段。保护的商品、Presence、价格历史、事件、生命周期、观测、事实版本、CURRENT本地化和西语字段均未改变。同批实际 Resume 分别20、19条 NO_OP，重复Apply0。后一批 Resume 使用有限锁等待，未干预其他任务。当前继续处理阶段累计完整验收353字段/309SKU：缺失补译3、已有中文修正31、正确原值审批及来源绑定恢复319。其他任务 current_catalog_fix 提交不计入本阶段 Apply。

全范围仍为原3196 SKU的19176字段。累计独立审核733个初始pending字段，380个保留待复核。旧证据核验在对应报告的PRIMARY head完成：2617个独立语义审核字段、4674个批准类目映射字段可复用；这7291字段不是新增审核、生成、审批或Apply。来源/目标/Registry不符的29字段未计入复用，当前均CURRENT，历史自主审批授权不覆盖它们。后续 head 变化时仍须验证对应上下文和证据有效性。

SKU3218650实际详情包含3 kWh/1000h，正确等价渲染3千瓦时/1000小时曾触发PROTECTED_TOKEN_MISSING。现仅在QA比较视图中将千瓦时/瓦时/毫安时/安时归一为kWh/Wh/mAh/Ah；不换算数值，不改原来源、中文、Hash、provider占位符或缓存。相同数字和相同单位维度/次数继续逐项比较；错值、错维度、重复数量和型号变化仍阻断。该商品A color属性原有HIGH来源歧义保留，机械PASS不创建审批或Apply。

新增真实来源fixture及18个回归用例覆盖等价单位、重复/遗漏/额外数值、单位维度、型号保护，以及原生HIGH审批Gate。测试为CI_SAFE：只读仓库fixture并调用纯QA/审批分类函数，不读生产数据、不访问官网、不调用模型。完整回归结果另存运行报告；本修改仅在本地独立分支实现，未push、未部署daily主线，不宣称线上全量daily已验证。

最终完整本地回归1430项通过（104.82秒），18项新容量单位回归通过；此前一次清单更新时序失败及一次Windows本地HTTP10053瞬断日志均保留，HTTP文件单独重跑4项通过。相同当前数据/同候选/同事实抽取对比原4a902e2与修正后QA：380待复核字段中356有候选，只有3218650详情由FAIL变PASS，380字段全部保持待审批/来源复核，不因机械PASS入库。运行证据：capacity_units_full_pytest_final_20261011.txt、owner380_same_snapshot_capacity_qa_comparison.json及两批Apply/Integrity/Resume报告。

## 来源绑定描述 QA 与长度单位漏检

剩余27个可信缺失描述已完成独立审查：12个来源支持的修正方案、1个可保留候选、14个来源复核。随后两组缺失品名各20字段完成独立审查：合计11个修正方案、8个可保留候选、21个来源复核。这些候选均保留此前HIGH审批及来源lineage，实际Apply0，不作为补译入库结果。累计独立审查822个初始pending字段，469个待审批/来源复核；可信缺失品名尚有67字段未审查，全19176字段语义范围仍未完成。此前实际Apply353字段/309SKU不变。

真实描述3000357完整保留Spider-Man时，残留扫描拆分连字符后误报；现仅允许同字段完整源绑定的该IP拼写及其扫描片段，未源绑定、残留西语正文或只有片段均继续阻断。3007114正文同时明确墙灯及房屋周围照明，功能词iluminación允许译为照明，不再机械要求照明灯；例外要求两个完整短语均在本字段，不能借标题或类目授权。2537232被套正文的照明用语仍保持QA阻断，未为不确定语义添加豁免。

反例测试还复现5 metros误译5厘米未被阻断：原单位检查未识别西语长度全称。新增量化metro/centímetro/milímetro/kilómetro单复数识别，映射到既有m/cm/mm/km单位检查，不进行数值换算，不匹配型号子串或误将面积/体积当长度。真实来源fixture、错误数值/单位、无来源IP、片段/西语残留、跨字段上下文、灯具主体事实、HIGH原生审批共31项新增CI_SAFE测试通过；完整回归1461项通过（101.07秒）。测试只读仓库fixture及正式种子字典，不读生产库、不访问官网、不调用模型、不写正式数据。

相同PRIMARY快照、目标和知识抽取下，对比c290eaa与修正后QA：449待复核字段中425个上下文一致可比较，仅3000357和3007114描述由FAIL变PASS；所有449仍待审批/来源复核，新增Approval和Apply均为0。比较报告初版完整提交号因辅助脚本替换字符串而错误，原文件保留；verified报告用git解析提交号并验证QA源码SHA后修正元数据，比较行不变。运行证据：owner449_same_snapshot_description_qa_comparison_verified.json、description_qa_full_pytest_20261011.txt、missing_names_next20b_review_acceptance.json。本修改仅本地独立分支实现，未push、未部署daily；完整线上daily兼容性仍待验证。

## 详情增量审查与连接器介词 QA

详情批次11/12/13均完成正式Apply、Master SUCCESS、独立保护表完整性检查和同批Resume。分别18/35/37个字段入库，实际中文文本改变分别0/2/3个；其余正确文本仅恢复审批与来源绑定。三个批次Resume重复Apply均0，Presence、价格历史、事件、生命周期、观测、西语、事实版本及CURRENT本地化均保持一致。累计本阶段完整验收443字段/371SKU：3缺失补译、36已有文本修正、404原值重绑定。候选、运行中或外部任务的提交不计入该累计。

新审查的60个详情字段中51KEEP、6CORRECTED、3HIGH待来源复核；再审查40字段，34KEEP、4CORRECTED、2REVIEW_REQUIRED。其中100 hojas误译100页的修正虽有明确来源支持，但改变计数单位，保持HIGH并等待Owner；防蝇罩的婴儿睡袋属性、3件/6件数量角色同样保留复核。机械QA不为这些问题创建审批。

SKU3218230官方详情的完整连接器值`Tipo de conector: POR CABLE`被保守技术词扫描识别出POR，正确有线译文因此触发PROTECTED_TOKEN_MISSING。现仅在既有QA扫描视图中移除该完整结构值的介词POR，保留CABLE检查及同字段其他POR型号/键标记。要求明确属性键、冒号、完整值及字段分隔边界，不为裸POR、型号POR、POR CABLE PLUS、其他字段短语或其他连接器类型增加通用豁免。原文、证据、Hash、provider请求和缓存不变。

真实fixture及新增19项CI_SAFE回归覆盖原误报、源证据、真实POR身份、同字段混合角色、跨字段隔离、接口/型号保护和SKU数值损坏。新测试只读仓库fixture并调用纯QA，不读生产库、不访问官网、不调用模型或写正式数据。针对性测试含既有详情扫描共42项通过；完整1480项回归通过（102.62秒）。相同PRIMARY快照、相同候选和语义事实下，1049字段全部可比，仅3218230由FAIL变PASS，其他1048结果不变，没有因此生成新的Approval或Apply。运行证据为`connector_preposition_full_pytest_20261011.txt`和`connector_preposition_same_snapshot_qa_comparison.json`。

目标仍为原3196SKU的19176字段。1049个初始pending字段已独立审查；此前2617个语义审核字段和4674个受控类目映射字段已重新核验，可复用但不计新增审核/Apply。全量语义审核尚未完成。当前目标队列3185SKU/8303字段，其中中文缺失4624；待复核和QA就绪候选必须按运行报告的互斥字段队列分别统计。本修正仅在本地独立分支实现，未push、未部署daily。

## 纠错用品类型与橡胶材质的字段角色

第14批57详情字段完成正式Apply、Master SUCCESS、完整性检查PASS和57条Resume NO_OP，重复Apply0。中文文本实际改变6字段，其余51保留正确原值并更新绑定；保护的商品、价格、事件、观测、生命周期、事实版本、CURRENT本地化和西语均一致。累计本阶段完整验收500字段/404SKU：缺失补译3、已有中文纠错42、原值绑定恢复455。新的80详情字段已独立审查：63KEEP、10CORRECTED、7REVIEW_REQUIRED；其中8个HIGH保持来源/Owner复核，不因机械QA放行。72个普通QA合格字段待后续正式审批/Apply，全量19176字段语义范围仍未完成。

真实SKU3223678详情`Tipo de dispositivo de corrección: Goma`被程序当作橡胶材质，正确的纠错用品类型橡皮擦反而触发SEMANTIC_FACT_DROPPED。现仅在同字段唯一完整类型键、完整值Goma时生成PRODUCT_TYPE橡皮擦事实，移除该具体出现所导致的错误橡胶材质事实。单独Material Goma、其他键、另一个字段的提示、限定值、重复类型键不会获得此规则；同字段另有橡胶材质时仍要求保留。原文、数值、来源Hash和字段版本不变，未发布字典或新增服务调用。

原错误中文矫正器械类型橡胶的修正涉及商品类型，保留HIGH与Owner审批，不自主Apply。修复不是给橡皮擦译文豁免材料QA，而是纠正源事实角色，错误橡胶、橡皮筋或胶棒目标继续失败。普通与历史语义模式都覆盖本明确结构字段，未来daily复用同模块。

新增24项CI_SAFE回归只读仓库fixture并调用纯语义、QA和原生审批分类；覆盖原错例、字段角色混合、重复/未知键、跨字段隔离、数值损坏、HIGH审批和来源Hash不变。首轮测试使用错误对象属性target_text导致2个测试失败，修正为实际canonical_value后24项通过；连同既有名称/详情60项针对性检查通过。完整1504项回归通过（100.37秒）。同PRIMARY快照和同候选比对55dc67f与新语义：1129个审查字段中1105有候选且可比，24无候选未计入，仅3223678详情由FAIL变PASS，新增Approval/Apply均0。

证据：`correction_device_goma_full_pytest_20261011.txt`、`correction_device_goma_same_snapshot_semantic_comparison.json`、`correction_device_goma_real_qa_reproduction_and_experiment.json`及第14批Apply/Integrity/Resume报告。代码仅本地独立分支实现，未push、未部署daily；本地回归不代表线上全量daily已验证。
