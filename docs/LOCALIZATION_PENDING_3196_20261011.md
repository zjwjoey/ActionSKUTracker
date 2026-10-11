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
