# Action Translation System V1

This document is the production-facing architecture for the Spanish-to-
Chinese localization path.  It describes ownership and gates; it is not a
second implementation.

## Architecture and ownership

```text
Action official Spanish facts
        ↓
source normalization + SOURCE_HASH_V1
        ↓
SQLite translation_source_versions / translation_units
        ↓
TranslationResolver
  manual → approved revision → exact TM → normalized TM → context TM
  → scoped terminology/rules → qwen-mt-flash → human review
        ↓
typed protected-fact restoration + target normalization + Typed QA
        ↓
append-only revision / review / immutable patch
        ↓
approved field-level localization projection in SQLite PRIMARY
        ↓
Master and Excel exports (read-only projections)
```

Spanish name, categories, specification, description, details, prices and
links are official facts.  The translation registry owns Chinese revisions,
provider provenance, QA and approval.  Master and Excel never become a second
translation database.

## Resolver and approval

`TranslationResolver` is the formal decision point.  It returns provenance
for every field and never treats `PENDING`, `STALE`, `FALLBACK_ES` or an
unreviewed model response as approved.  Fuzzy TM is suggestion-only.  A
provider response is a field-level candidate; each field can be QA-approved,
repaired, rejected or approved independently.

`auto_approval_enabled` and both production-apply switches default to false.
An approved revision must have the current source hash, `qa_status=PASS`, no
open blocking finding, and an explicit Owner or permitted policy decision.
Repairs append a new revision with `parent_revision_id` and
`repair_reason`; the old revision is retained.

## TM and terminology

The additive SQLite registry stores exact, normalized-exact and context-aware
TM entries.  Normalized matches carry `match_type` and
`normalization_version`; fuzzy suggestions never auto-apply.  Terminology is
scoped by product type, category 2, category 1, field and global scope, then
ordered by approval, priority and longest match.  Only relevant approved
terms are sent to Qwen (bounded by configuration).

Legacy CSV dictionaries remain source assets.  The importer/compatibility
resolver reads them into the same priority model; no legacy file silently
overrides an approved registry revision.

## Protected facts and QA

The provider adapter protects typed facts including SKU, EAN, URL, model,
technical token, number, quantity, unit, dimension, range, voltage, power,
capacity, frequency, weight, volume, size, certification and HTML.  Validation
checks placeholder id/type/value, multiplicity and order.  A missing,
duplicated or changed protected fact is a BLOCKER.

Typed QA also covers required fields, source-hash freshness, numeric/unit/model
preservation, fixed category values, Spanish/English/HTML residue,
terminology violations, SKU/EAN mismatches and hallucinated facts.  BLOCKER
or ERROR findings cannot be approved or written to PRIMARY.

## Qwen provider contract

`QwenMTProvider` is a translation provider, not the system brain.  The native
DashScope HTTP payload uses `input.messages` and
`parameters.translation_options`; the OpenAI-compatible payload uses a single
user message and top-level `translation_options` (the SDK calls this
`extra_body`).  Both include source/target language, scoped terms, TM examples
and domain.  Batch limits, character limits, timeout, retry count and rate
limit are configuration values.  Automatic retry is limited to network,
timeout, 429 and 5xx.  4xx, malformed/empty responses, language failures and
protected-token failures are terminal.

API keys are read only from environment variables and never enter logs,
reports, SQLite or Git.  CI uses the Fake provider and HTTP fixtures; live
smoke is explicit and is not required for a code-only pass.

## Daily, queue and incremental behavior

The official Daily commit is independent from translation.  After source
commit, registry ingest creates idempotent field-level queue items.  New
SKUs queue all available fields; unchanged source versions reuse their
approved revisions; a changed source marks prior units/revisions stale and
queues only changed fields.  Queue claiming is transactional and records
`PENDING`, `CLAIMED`, `RETRY`, `COMPLETED`, `FAILED` and `BLOCKED` with retry
metadata.  `RETRY` is reserved for network/timeout/429/5xx and transient
SQLite I/O; 400/401/403, malformed or empty responses, protected-token
failures and deterministic QA/data blockers are terminal (`FAILED` or
`BLOCKED`) and are never re-enqueued indefinitely.  Retry count is bounded
by the queue maximum. Provider failure never rolls back the official Spanish
commit.

Every real provider attempt is written to `translation_provider_calls`,
including failed attempts.  A successful provider revision stores the
`provider_call_id`, provider/model, request/response hashes, request id,
usage and retry count in structured revision provenance.  Deterministic,
TM, terminology and manual resolutions do not fabricate provider calls.

Resolver-selected approved terminology is retained in the resolution
provenance, sent to Qwen only as the official `{source,target}` pair, and
passed again to Typed QA.  A required approved term missing from the target
is a blocking `TERMINOLOGY_VIOLATION`.

The legacy `apply_zh` function remains only as a compatibility adapter for old
fixtures.  The formal Daily path uses `apply_zh_formal`: missing Chinese stays
empty/PENDING and is displayed as an explicitly marked ES fallback only at the
presentation boundary, never as an approved Chinese value.

## Export and Master

Export is read-only.  It reads the approved PRIMARY projection and does not
call a provider, rewrite the registry or reinterpret Spanish facts.  Missing
approved fields are either empty or explicitly marked `display_fallback=ES`
per the export profile.  A Qwen candidate cannot write Master directly.

The Action display profile is `ACTION_MASTER_NO_BRAND_V1`: brand/IP evidence
is retained internally for provenance and QA, but Chinese display omits those
tokens and never adds `牌`.  Removal is span/token based, not a global string
replacement, so ordinary words such as `扑克牌` remain valid.

## Migration, reports and operations

Migration is preview-first and hash-bound.  Preview produces eligible,
rejected, conflicts, context-only and manifest artifacts; apply requires the
exact manifest hash and an explicit commit flag.  It adds registry evidence,
not production values.

Shadow and canary reports contain a summary, field units, manifest and
production-write flag.  The formal report contract additionally supports
TM hits, terminology hits, provider calls, QA findings, blocked units and
review-required units.  Reports must be reproducible from run id, source hash,
policy version and dictionary/registry hashes.

Frozen, Owner-approved evaluation is offline and read-only:

```text
python -m action_tracker translation-gold-eval \
  --input <gold-predictions.csv-or-jsonl> --output <evaluation.json>
```

It reports field exactness, number/unit/model preservation, Spanish residue,
category validity and QA findings without promoting any row.

## Safety defaults and rollout

```text
localization.ai.enabled = false
localization.production_apply_enabled = false
localization.auto_approval_enabled = false
knowledge.production_apply_enabled = false
```

The rollout order is shadow → canary → Owner review → immutable patch preview
→ explicit production apply.  A live provider key, Owner approval and an
external production database are operational prerequisites and are reported
as external validation items rather than silently bypassed.
