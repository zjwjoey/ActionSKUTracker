# Translation Runtime V1 — implementation contract

This branch implements the Translation System V1 runtime contract without
enabling production writes.  The implementation is fail-closed: a provider
candidate is never an approved Chinese value until field-level QA, freshness
and Owner approval pass.

* `localization/providers/qwen_mt.py` is a dedicated `qwen-mt-flash` adapter.
  It sends one user message, uses `translation_options` for terms/TM/domain,
  has separate DashScope-native (`input` + `parameters`) and OpenAI-compatible
  payload builders, retries only network/429/5xx failures, rejects empty or
  clearly untranslated responses, and reads `DASHSCOPE_API_KEY` from the
  environment.
* `localization/protection/tokens.py` protects URLs, SKUs, model identifiers,
  numbers and units. A response with a missing or changed placeholder fails
  closed instead of entering a candidate queue.
* `localization/qa.py` is a deterministic candidate guard for required fields,
  Spanish/HTML residue, categories and numeric preservation.
* `localization/registry` and the additive SQLite schema store immutable source
  versions, field units, append-only revisions/events, provider calls, QA
  findings, TM and scoped terminology. These records are not a production
  Apply path until the explicit immutable-patch gate is used.
* `localization/runtime_builder.py` is the single construction boundary for
  Registry, Knowledge/Engine, TM/terminology-backed Resolver, Provider and
  Queue Worker. Shadow, Canary and Worker do not carry separate wiring.
* `localization/hashes.py` documents the compatible `SOURCE_HASH_V1` contract
  and exposes a new deterministic V2 helper without rewriting historical V1
  hashes.

The configuration remains `enabled: false`, `production_apply_enabled: false`
and `auto_approval_enabled: false`. A real API call requires an explicit
operator change and an environment key; no credential is committed.

The formal path now has one field-level `TranslationResolver` with this
priority: manual lock, approved revision, exact/normalized/context TM,
scoped terminology/deterministic rules, then an explicitly enabled Provider.
The Daily adapter uses `apply_zh_formal`: missing Chinese stays empty/PENDING
and is queued; Spanish is never persisted as approved Chinese. Export remains
read-only against the approved PRIMARY projection.

Source refresh is field-level: unchanged `source_text_hash` fields reuse a
fresh approved revision in the new source version, while only changed fields
become stale and are re-enqueued. The explicit `translation-worker` consumes
that queue through the resolver and records append-only Registry revisions; it
never writes `product_localizations`.

Queue states are `PENDING`, `CLAIMED`, `RETRY`, `COMPLETED`, `FAILED` and
`BLOCKED`. Retry is bounded and only used for retryable provider/network
errors or transient SQLite I/O. Terminal provider errors (400/401/403,
invalid/empty response and protected-token mismatch) become `FAILED` or
`BLOCKED`; unknown runtime errors become `FAILED`. A provider result that
passes Typed QA is recorded as `REVIEW_REQUIRED`; the worker never
auto-approves Qwen.

Provider calls are append-only audit rows for both success and failure. The
worker links a successful revision to its `provider_call_id` and stores the
real provider/model/request id, request/response hashes, usage and retry
count. Non-provider resolutions use their own resolution source and do not
pretend to be Qwen calls. Resolver-selected terminology is carried through
the Qwen request and the same selected terms are supplied to Typed QA.

The only Registry-to-production route is:

```text
fresh QA-PASS revision + explicit Owner approval
        ↓
PATCH_CREATED + PATCH_APPROVED (immutable)
        ↓
explicit translation-apply --from-registry --commit
        ↓
SQLite PRIMARY product_localizations
        ↓
read-only Master/Excel export
```

The apply command rechecks source freshness, current revision, QA blockers,
approval status and the configured base commit. Both `knowledge` and
`localization` production-apply flags remain false by default.

Read-only operational commands are available:

```text
python -m action_tracker translation-status
python -m action_tracker localization-shadow-run --output <dir>
python -m action_tracker localization-canary --sku <SKU> --field name --output <dir>
python -m action_tracker localization-live-smoke --output <report.json>
python -m action_tracker translation-worker --limit 50
```

Migration apply is hash-bound and requires `--commit`; it only creates
approved registry TM entries and never writes PRIMARY, Master or Dictionary.

The formal configuration source is `localization:`. Older `translation:` and
`ai:` blocks are compatibility-only and are not a second provider path.

For an explicit shadow registration run use:

```text
python -m action_tracker localization-registry-ingest \
  --run-id <official-observation-run> \
  --observed-at <ISO timestamp>
```

This registers source versions and creates field-level translation queue
items only. It does not apply a Chinese value, update Master, or promote a
model result.

TM/Gold/Patch migration is likewise preview-first:

```text
python -m action_tracker localization-migration-preview \
  --input <tm.csv> --input <gold.xlsx> --input <patches.csv> \
  --output <preview-directory>
```

The command emits `eligible.csv`, `rejected.csv`, `conflicts.csv`,
`context_only.csv` and a hash-bound `manifest.json`. Missing source hashes,
missing identity/targets, unapproved statuses and multiple approved targets
for the same `(SKU, field, source_hash)` are excluded. Context fields and
approval evidence are preserved. Apply requires the exact manifest hash and
an explicit `--commit`; it only inserts registry TM entries and never writes
PRIMARY, Master or Dictionary.

## Validation boundary

CI never calls a live provider.  The repository contains a deterministic Fake
provider and HTTP fixtures for native/compatible payloads, retryable 429,
empty/invalid responses, and protected-token restoration.  A live call is an
operator-controlled smoke test only:

```text
LIVE_QWEN_API_NOT_VERIFIED
```

is the correct status when `DASHSCOPE_API_KEY` is absent.  A production
database outside the repository may also require an explicit registry
migration before its first shadow run; that is an operational validation, not
a reason to weaken the code gate.
