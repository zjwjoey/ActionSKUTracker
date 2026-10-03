# Product Family Governance V1

This module adds a deterministic business-language layer to Translation System
V1. It does not create a second translation pipeline and it never performs
production Apply or automatic approval.

## Contract

`ProductFamilyRegistry` owns versioned policies. The first policy is
`CLEANING_CLOTH_V1`, covering phrase-aware Spanish aliases such as `paño de
microfibra` and `bayeta`. Classification is fail-closed: a bare ordinary noun
without independent category, semantic, or multi-word phrase evidence remains
`UNKNOWN`.

`TranslationContext` is built once from the official Spanish source and
semantic facts, then carried through Resolver, TM, scoped Terminology, Qwen,
QA, Repair, Runtime, Shadow, and Canary. Its provenance contains SKU,
source-hash, family/policy version, field, detail context, and semantic facts.

## Trust and freshness

Only `LOCKED`, `HUMAN_REVIEWED`, or the existing equivalent reviewed statuses
are authoritative Product Dictionary input. `MODEL_TRANSLATED`,
`LEGACY_UNVERIFIED`, and `NEEDS_REVIEW` remain suggestions. A reviewed row is
still rejected as authoritative when its official `source_hash` is stale;
locked stale rows are reported as `STALE_LOCKED_KNOWLEDGE`.

## Canonical QA

Fact QA and Canonical QA are separate. Canonical QA enforces family naming,
field terminology, and structured detail context without exact-matching free
description prose. For the cleaning-cloth family, names use `清洁布`, name
`microfibra` uses `微纤维`, while description and `details.material` use
`超细纤维`; `Goma` in material is `橡胶` and `gomas` in a description is
`橡皮筋`. Conflicting scoped terminology fails closed as
`TERMINOLOGY_CONFLICT`.

Canonical correctness is independent of Production Enforcement. A finding
always makes `canonical_qa_status=FAIL`, even in Shadow/Canary or Worker mode;
`blocking` is only a stage-flow hint. The Worker persists a Fact-QA-PASS plus
Canonical-QA-FAIL revision as `REVIEW_REQUIRED` and completes the queue for
human correction. Only policy/terminology conflicts remain queue `BLOCKED`.
Approved Projection and Production Apply require:
`qa_status=PASS`, `canonical_qa_status in (PASS, NOT_REQUIRED)`, fresh source,
approved review status, and no open blocker/error/high finding. Manual
corrections must create a new revision and rerun both QA layers; a failed
revision cannot be approved.

## Reports and safety

Use `translation-family-audit`, `translation-family-regression`, and
`translation-family-feedback` for read-only audit, regression, and candidate
mining. Feedback mining produces review candidates only; it never auto-
approves or writes PRIMARY. Shadow/Canary reports include family, policy,
context, fact-QA, and canonical-QA fields. Database migrations are additive
and idempotent; production Apply and auto-approval remain disabled.
