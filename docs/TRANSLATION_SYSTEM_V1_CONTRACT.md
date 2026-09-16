# Translation System V1 Contract

## Data ownership

* Spanish name, categories, specification, description, details, prices and
  links are official source facts. Translation code must never rewrite them.
* Chinese fields are derived field-level revisions. A SKU-level status is not
  sufficient to approve a field.
* SQLite PRIMARY is the source of truth; Master and Excel files are projections.

## Hash contracts

* `PRODUCT_DICTIONARY_HASH_V1`: the legacy four-field product dictionary hash.
* `LOCALIZATION_SOURCE_HASH_V1`: `name_es`, `cat1_es`, `cat2_es`, `spec_es`,
  `desc_es`, `details_es`, using the frozen UTF-8/trim/0x1f SHA-256 contract.
* `RAW_FACT_HASH_V1` and `NORMALIZED_FACT_HASH_V1`: deterministic JSON hashes
  for source evidence. A new canonicalization rule must create a new version;
  historical hashes are never rewritten.

## Chinese display policy

The current Action Master profile is `ACTION_MASTER_NO_BRAND_V1`:

* Chinese display text omits brand and IP names.
* The suffix `牌` is never added.
* Models, interfaces, technical tokens, numbers, units and SKU facts remain.
* Brand/IP knowledge remains in internal provenance and QA, but is not emitted.

## Translation order

`manual field lock → approved product revision → scoped TM → scoped
terminology → deterministic rules → Qwen candidate → review`.

Qwen candidates are never production values until field-level QA, freshness,
Owner approval and the existing immutable patch gate pass. Empty or fallback
Chinese remains explicitly pending; it is not silently treated as approved.
