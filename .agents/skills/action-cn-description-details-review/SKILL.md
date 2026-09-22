---
name: action-cn-description-details-review
description: Model-led semantic review of Action Spanish descriptions and structured details; never review name/category/spec.
---

# Action Description / Details Semantic Review

Version: 2026-09-21

This Skill is a `MODEL-LED MTPE SEMANTIC REVIEWER`. It compares
`source_es` and `qwen_zh` independently for `description` and `details`. It
never writes Master or production state and does not modify name, categories,
or specification.

## Field contract

`desc_es` is the only fact source for `description_zh`; `details_es` is the
only fact source for `details_zh`. Other fields are context only. Empty source
is `NO_SOURCE` and must remain empty. Never import dimensions, quantities,
capacity, product number, batteries, or technical values from another field.

## Semantic checks

Review product/attribute identity, terminology, omission/addition, numbers,
units, quantities, dimensions, models, series, brand/IP/standards,
certifications, negation, compatibility, and details key/value semantics. For
details preserve source order and duplicate keys. `Voltaje` means voltage,
`Potencia` means power, `Contenido` means content, and boolean/negation values
must retain their meaning. A fluent but semantically wrong key is `CORRECTED`.

Description and details are `SOURCE_FAITHFUL`: preserve source brands/IP and
never apply the name no-brand deletion rule. Do not create dangling fragments
such as `可选、`, `和等`, or `《》`.

## Guard boundary

**GUARD PASS IS NOT TRANSLATION PASS.** Guard only checks deterministic facts,
schema, source-empty, units/numbers/tokens, Spanish residual, and freshness.
Guard PASS cannot create `KEEP`. `KEEP` requires independent semantic review;
ordinary errors are `CORRECTED`; only source conflict, ambiguity, or unsafe
technical meaning is `REVIEW_REQUIRED`.

Record `review_model=CODEX`, `qwen_calls_for_review=0`, `master_writes=0`,
and `production_apply=false` for every review batch.
