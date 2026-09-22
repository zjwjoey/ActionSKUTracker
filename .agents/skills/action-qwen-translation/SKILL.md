---
name: action-qwen-translation
description: Field-scoped Action Spanish-to-Chinese candidate generation for name, category, specification, description, and details.
---

# Action Qwen Translation Contract

Version: 2026-09-21

This Skill generates candidates only. It never writes Master, Dictionary,
SQLite PRIMARY, or production state. Qwen calls and review calls are recorded
separately; a Guard result is never a semantic translation decision.

## Field contract

For every target field, the matching Spanish field is the `PRIMARY SOURCE`:

| target | primary source |
|---|---|
| name_zh | name_es |
| cat1_zh | cat1_es plus the fixed category mapping |
| cat2_zh | cat2_es plus the approved category mapping |
| spec_zh | spec_es |
| description_zh | desc_es / description_es |
| details_zh | details_es |

All other product fields are `CONTEXT FOR DISAMBIGUATION` only. Context may
clarify an ambiguous noun, but it must not contribute a new quantity, size,
capacity, article number, battery count, model, voltage, power, material, or
other fact to the translated field. If the primary source is empty, output
empty and decision `NO_SOURCE`; never use another field as fallback.

## Policy

- Name is `NO_BRAND` by default unless the active naming policy proves the
  brand/IP/model is required to identify the product.
- Description and details are `SOURCE_FAITHFUL`; preserve source brands, IP,
  series, models, standards, and certifications.
- Never run generic brand deletion after natural-language translation.
- Only normalize numeric dimensions with `(?<=\d)\s*[xX]\s*(?=\d)`.
  Protect `XL`, `XXL`, `XXXL`, `USB-C`, `CR2032`, `A3`, `A4`, `IP44` and
  equivalent technical tokens.

## Review boundary

`GUARD PASS IS NOT TRANSLATION PASS`. Guard checks schema, source-empty,
numbers, units, technical tokens, residual Spanish, and freshness. It does not
decide product identity, terminology, omissions, additions, negation,
compatibility, or details key/value meaning. Those require the separate
Description/Details semantic review Skill or an explicit reviewer decision.

Allowed decisions are `KEEP`, `CORRECTED`, `REVIEW_REQUIRED`, and `NO_SOURCE`.
`KEEP` requires an independent semantic comparison; ordinary translation
errors should be `CORRECTED`, while source conflicts/ambiguity are
`REVIEW_REQUIRED`.
