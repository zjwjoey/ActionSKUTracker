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

The single `TranslationResolver` is the only formal decision point:

`manual field lock → approved revision (same source hash) → exact TM →
normalized exact TM → context TM → scoped terminology/rules → qwen-mt-flash
candidate → human review`.

Fuzzy TM is suggestion-only and can never silently become a production value.

Qwen candidates are never production values until field-level QA, freshness,
Owner approval and the existing immutable patch gate pass. Empty or fallback
Chinese remains explicitly pending; it is not silently treated as approved.

## Registry state contract

| Layer | States | Meaning |
|---|---|---|
| `translation_units.status` | `PENDING`, `STALE`, `REVIEW_REQUIRED`, `APPROVED`, `BLOCKED` | Current field decision state |
| `translation_units.freshness_status` | `FRESH`, `STALE` | Whether the field source hash matches the current source version |
| `translation_revisions.qa_status` | `PENDING`, `PASS`, `FAIL` | Deterministic candidate guard result |
| `translation_revisions.review_status` | `PENDING`, `APPROVED`, `HUMAN_REVIEWED`, `LOCKED`, `REJECTED`, `STALE` | Explicit field-level Owner decision |
| `translation_queue.status` | `PENDING`, `CLAIMED`, `RETRY`, `COMPLETED`, `FAILED`, `BLOCKED` | Worker lifecycle |

Queue failure policy is fail-closed: retryable provider errors are network,
timeout, HTTP 429 and HTTP 5xx; transient SQLite I/O may retry. HTTP 400,
401, 403, invalid/empty/unexpected-language responses and protected-token
errors are terminal and cannot loop. Data/source mismatch and QA blockers
are `BLOCKED`; unknown program exceptions are `FAILED`. `COMPLETED` means
automatic processing produced a revision, not that it was Owner-approved or
written to PRIMARY.

`translation_revisions.provider_call_id` and `provenance_json` link a
provider-backed revision to the append-only provider call containing
provider, model, request id, request hash, response hash, usage and retry
count. Deterministic, TM, terminology and manual resolutions retain their
own `resolution_source` and never create a fake provider call.

Approved terminology selected by the Resolver is retained in provenance,
projected to Qwen as only `{source,target}` pairs, and passed to QA. Missing
approved targets raise blocking `TERMINOLOGY_VIOLATION`; forbidden targets
raise `FORBIDDEN_TERM`.

Only a fresh, QA-PASS, explicitly approved current revision can be projected
into PRIMARY. Queue workers and providers never write the PRIMARY projection.

## Runtime and safety boundary

`build_translation_runtime()` is the single wiring point used by Shadow,
Canary and Queue Worker. Shadow never allows provider calls; Canary requires
an explicit provider flag and remains read-only. `localization.ai.enabled`,
`localization.production_apply_enabled`, `knowledge.production_apply_enabled`
and `localization.auto_approval_enabled` are all false by default.

Protected fact types implemented by the current token engine are URL, HTML,
SKU, EAN, MODEL, TECH, CERTIFICATION, MONEY, CAPACITY, POWER, FREQUENCY,
VOLUME, WEIGHT, UNIT, PERCENT, RANGE, DIMENSION and NUMBER. Typed QA fails
closed for protected-token, numeric, unit, model, category, terminology,
Spanish-residue, HTML and required-field violations.
