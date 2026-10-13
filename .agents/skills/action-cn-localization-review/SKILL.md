---
name: action-cn-localization-review
description: "Review Qwen Spanish-to-Chinese Action product translations with minimal, evidence-based corrections and owner-review routing; never write Master or production data."
---

# Action CN Localization Review

Use this skill only for the independent Localization Lite side-channel. The input is an official Spanish field plus a Qwen Chinese candidate and the same SKU's Spanish context. The output is a field-level decision:

- `KEEP`: the Qwen result is faithful and usable.
- `CORRECTED`: make only a clearly evidenced correction.
- `REVIEW_REQUIRED`: ambiguity remains; do not guess.

Apply the following review priority for Lite V2/V3:

1. **P0 — `name`**: review every sampled SKU in depth. Use `cat1`, `cat2`, `spec`, `description`, and `details` as identity context. Confirm the product object, necessary model/size/capacity, no-brand display policy, and naming rules. If the correction is reliably supported by the source/context, use `CORRECTED`; reserve `REVIEW_REQUIRED` for genuine ambiguity.
2. **P1 — `cat1`, `cat2`, `spec`**: perform a light factual check for category, numbers, units, models, dimensions, and capacity. Keep correct values without style edits.
3. **P2 — `description`, `details`**: anomaly-scan only. Keep translations that are faithful, understandable, and fact-complete. Correct only clear mistranslation, material omission/addition, numeric/model error, substantial Spanish/HTML residue, severe grammar damage, or duplicate facts.

For Lite V3, use the same 150-SKU source snapshot and the original `qwen_zh` without calling a provider. The V2 assistant audit is regression evidence, not a SKU-specific replacement table. Generalize its cases into behavior: remove known display brands/series while retaining models and product identity; use category/spec/description/details to disambiguate the product noun; preserve A4, Fxx, XL, capacities, and other necessary identifiers; and treat Spanish decimal commas, `x`/`×`, and unit conversions as equivalent numeric formats before raising a P1 issue. A reliable correction is `CORRECTED`; only unresolved, genuinely ambiguous product identities remain `REVIEW_REQUIRED`. Keep P2 at anomaly-scan strength and do not rewrite for style.

Always preserve `source_es` and the original `qwen_zh`; write V1 and V2 reviewed values separately as `reviewed_zh_v1` and `reviewed_zh_v2`, with `decision_v1` and `decision_v2`. Apply `MINIMAL_POST_EDIT`: never rewrite for style, invent selling points, or move facts between fields. Preserve numbers, models, units, interfaces, and product identity. Brand/IP removal follows the current no-brand display-name policy, but models and necessary identifiers stay.

Read `references/naming_rules.md` for stable rules and `references/terminology.csv` for approved terms. Record every non-KEEP decision with a stable correction type and a short evidence note. The skill may create review artifacts and observations only; it must not modify Resolver, Registry, Family, QA, Wave Gate, Master, Dictionary, or Production Apply logic.

## Boundary contract (2026-09-18)

This Lite review is field-local. Other Spanish fields may be read to identify the
product, but facts from `spec_es`, `description_es`, or `details_es` must not be
copied into `name_zh` unless the same fact is present in `name_es`. In particular,
do not prefix a name with quantities, dimensions, weight, capacity, power, lumen,
or other numbers taken only from another field. Models, interfaces, standards,
and sizes are retained only when they are present in the name source itself.

The display-name policy is no-brand by default. Remove brands, IP, and series
names from the Chinese display name while preserving the product object and any
source-supported model or technical identifier. Use context only to disambiguate
the product object (for example an art brush set is `画笔套装`, an LCD children's
drawing tablet is `儿童LCD绘画板`, and a clothing detergent is `洗衣液`). Do not
turn context into extra name facts.

P0 performs a complete product-identity review of `name_zh`; P1 lightly checks
categories and specifications; P2 only fixes clear anomalies in descriptions
and details. `CORRECTED` is valid only when the reviewed text differs from the
Qwen text; otherwise the decision is `KEEP`. This rerun reuses the existing Qwen
outputs and must make zero provider calls, zero Master writes, and zero production
applies.

## MODEL-LED V2 boundary patch (2026-09-18)

For the 278-SKU V2 rerun, keep the same frozen SKU/source snapshot and read the
Spanish source, the original Qwen output, and the V1 reviewed value. Do not
recrawl, call Qwen, write Master, or enable Production Apply. The purpose of V2
is to repair the four confirmed gaps without introducing a new resolver, family
system, registry, semantic gate, brand-regex system, or translation engine.

### P1 completeness contract

For `cat1`, `cat2`, and `spec`, a non-empty Spanish source must yield a usable
Chinese reviewed value. A Spanish passthrough or an empty reviewed value is not
`KEEP`. Use the existing approved category terminology for `cat1`; translate
`cat2` as its own field using category/context evidence; translate `spec` from
`spec_es` only, preserving all source numbers, models, units, sizes and
interfaces. Pure technical tokens may remain unchanged when they are genuinely
the source fact. Do not copy description/details facts into a name, category, or
specification.

### P0 final self-check

Before finalizing every `name` decision, ask: (1) does the name identify the
actual product object, (2) are display brands/IP/series removed under the
no-brand policy while models and necessary identifiers remain, and (3) did any
number, size, capacity or other fact move into the name from a different source
field? Only a reliable, evidence-backed correction is `CORRECTED`; unresolved
source ambiguity remains `REVIEW_REQUIRED`.

### P2 obvious-error self-check

Run a field-local self-check for `description` and `details` before `KEEP`.
Correct only clear semantic errors, product-object errors, Chinese/Spanish or
Chinese/English mixing, numeric/unit errors, material omissions/additions,
duplicate facts, HTML residue, or contextually wrong wording. Do not rewrite
correct prose for style. Recheck the known regression patterns: `petanca` is
the French-boules context rather than pets; `Tipo de dispensador` must be a
natural Chinese field label; `Sin planchado` must follow clothing-care semantics
(`无需熨烫` versus `不可/请勿熨烫` according to the source context), not a global
string replacement.

### Evidence and layer contract

Retain, for every field, the three comparison layers:
`source_es`, `qwen_zh`, `previous_reviewed_zh`, `new_reviewed_zh`,
`previous_decision`, and `new_decision`. Keep `product_identity_zh` as the
model's concise product-object conclusion and add a short, verifiable
`identity_evidence` summary for `CORRECTED`/`REVIEW_REQUIRED` rows (and for
high-risk `KEEP` rows when useful). `review_basis` must name the actual evidence
used (`NAME_ONLY`, `NAME_CATEGORY`, `NAME_SPEC`, `NAME_DESCRIPTION`,
`NAME_DETAILS`, `NAME_CATEGORY_DESCRIPTION`, `CAT1_POLICY_AND_SOURCE`,
`CAT2_DICTIONARY_AND_SOURCE`, `SPEC_FIELD_EVIDENCE`,
`DESCRIPTION_FIELD_EVIDENCE`, or `DETAILS_FIELD_EVIDENCE`); do not fill every
row with `FULL_SKU_CONTEXT`.

The V2 output is an independent owner-review artifact. Keep `owner_decision`
blank until the Owner acts. A normal, reliably fixable error must not be routed
to Owner Review merely because it is semantic; reserve `REVIEW_REQUIRED` for
genuine ambiguity or insufficient official source evidence.
