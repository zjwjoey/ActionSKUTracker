---
name: action-cn-localization-review
description: Action Chinese name/category/spec review with fixed category mapping, no-brand naming, field contracts, and safe technical formatting.
---

# Action Name / Category / Specification Review

Version: 2026-09-21

Scope is limited to `name`, `cat1`, `cat2`, and `spec`. Description/details
belong to `action-cn-description-details-review`.

- Each target field uses its own Spanish field as `PRIMARY SOURCE`; other
  fields are context only and cannot add facts.
- Empty source is `NO_SOURCE`, never a fallback translation.
- Name is `NO_BRAND` by default for ordinary brand names. Do not blanket-delete
  model, series, standard, interface, size, capacity, technical tokens, or
  identity-bearing IP/product-identity terms; preserve those product facts.
  Preserve dose and color facts as well.
- cat1 must use the fixed 15-category mapping and cat2 must use approved
  breadcrumb/category evidence.
- Preserve every number, unit, quantity and technical token in spec.
- Never use global `text.replace("x", "×")`; only numeric `x` between digits may
  become `×`. Protect `XL`, `XXL`, `XXXL`, `USB-C`, `CR2032`, `A3`, `A4`, and
  `IP44`.

`GUARD PASS IS NOT TRANSLATION PASS`. `KEEP` requires independent semantic
review. Ordinary errors are `CORRECTED`; source conflicts are
`REVIEW_REQUIRED`.
