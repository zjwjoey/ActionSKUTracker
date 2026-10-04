# Workflow V2 implementation audit

## Implemented

- Explicit stage contracts and a single `WorkflowContext` using
  `Europe/Madrid` business dates.
- Source completeness, new SKU and reappeared SKU gates.
- Deterministic source cleaning with raw/clean hashes and protected-number
  blocking.
- Translation planning by field source hash, reusing approved unchanged fields.
- Existing Translation System V1 provider request contract and Typed QA.
- Field-level auto-validation policy `QWEN_AUTO_VALIDATE_V1`.
- Offline ES audit, ZH audit and ES/ZH identity parity audit.
- Resumable state and required report artifacts.
- Development CLI `data-update-v2` with fixture and fake-provider switches.
- Isolated SQLite local canary covering registry approval and immutable Apply.

## Integrated in V1 closure

- Stage dependency map with persistent `BLOCKED_BY_DEPENDENCY` results and
  cumulative blocker evidence.
- Atomic state persistence after every stage; resume restores the serialized
  `WorkflowContext` and translation artifacts and can locate a run by `run_id`.
- Strict SKU completeness including `extra_skus`, plus separate Presence and
  Fact readiness states.
- Presence-only commits preserve existing reliable product and ES detail facts;
  incomplete listing fields cannot overwrite them with empty values.
- Detail-pending translation readiness is field scoped, and the export audit
  reuses Translation V1 Typed QA for Spanish residue, numeric and unit checks.
- Workflow execution uses the existing Translation System V1 Registry,
  `TranslationQueueWorker`, `TranslationResolver`, typed/canonical QA and
  immutable localization patch apply. The old direct-provider helpers remain
  only as deprecated V0.1 fixture references.
- Fact commit uses the real `CommitBundle` and `ProductionWriter` contract on
  an isolated temporary SQLite PRIMARY-like database.
- Detail planning consumes `product_detail_state` freshness and source hashes.
- Existing exporter row builders produce independent ES and ZH staging
  projections, validate the existing output-row contract, and publish them
  only behind a complete ES/ZH manifest. When normal project paths are
  supplied, `export_catalog()` also runs against the isolated canary DB and
  writes its formal files into the pending staging directory. Bilingual audits
  and parity checks run before that manifest is written.
- The exporter resolves the immutable localization-Apply commit head in the
  temporary SQLite database, so its formal-source guard reads the current
  canary projection rather than the earlier fact-commit run.
- Workflow V2 tests, including the provider/QA failure matrix, are listed in
  `tests/ci_safe_tests.txt`.

## Deliberately not enabled

## Fixture-only / production-disabled

The development path does not call the Action website or real Qwen API by
default. Canary execution requires an explicit temporary database and
`--canary`. An explicitly gated `production_apply` call now resolves only the
configured SQLite PRIMARY, creates a verified SQLite backup before writes, and
records the real localization commit ID. Its export stage also rebuilds the
PRIMARY compatibility projection against the current localization head and
publishes the validated ES/ZH formal pair with rollback of the previous pair
on failure. The configuration switches remain disabled by default and still
require a reviewed rollout.

The established daily collector is now available through the default
read-only extraction adapter. When explicitly enabled, the existing detail
retry/apply contract can enrich the isolated canary database. The adapter
still never targets the configured production database. Template 1
publication remains outside the canary because formal production artifacts
are still disabled.
