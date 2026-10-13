# Action Chinese Localization Lite — Stable Naming Rules

## Scope

These are review rules, not an automatic translation engine. When evidence is insufficient, use `REVIEW_REQUIRED`.

## Product name

- The name must identify the product object, not only a color, brand, series, or generic category.
- Do not put a brand/IP into the Chinese display name by default. Keep model numbers, standards, interfaces, sizes, capacities, and other identifiers required to distinguish the SKU.
- Examples: `Bolsa de regalo XL` → `XL礼品袋`; `Cuaderno A4` → `A4笔记本`; `Limpiaparabrisas Magnum F48` → `F48无骨雨刷片`.

## Categories

- Translate `cat1_es` and `cat2_es` as their own fields.
- Do not infer or replace a category from the name or a different field. If the source category is ambiguous, route it for review.

## Specification

- Translate only facts present in `spec_es`. Do not copy facts from name, description, or details into spec.
- Preserve every source number, unit, model, and range; normalize punctuation only when meaning is unchanged.

## Description and details

- Description is faithful, natural product prose without invented claims.
- Details retain attributes and units, use clear `字段: 值` structure where present, and do not duplicate or drop facts.
- Context matters: terms such as `pintura`, `gomas`, and `hojas` must be resolved from the supplied product context, not by a global replacement.

## Decisions

Use only `KEEP`, `CORRECTED`, or `REVIEW_REQUIRED`. Use correction types from the Lite contract (`MISTRANSLATION`, `PRODUCT_IDENTITY`, `BRAND_POLICY`, `MODEL_OR_SPEC`, `NUMERIC_OR_UNIT`, `TERMINOLOGY`, `CATEGORY`, `UNNATURAL_CHINESE`, `MISSING_FACT`, `DUPLICATE_FACT`, `FIELD_SCOPE`, `OTHER`).
