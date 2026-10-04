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
- Workflow V2 tests, including the provider/QA failure matrix, are listed in
  `tests/ci_safe_tests.txt`.

## Deliberately not enabled

## Fixture-only / production-disabled

The development path does not call the Action website or real Qwen API and
never resolves the configured production SQLite path. Non-dry execution
requires an explicit temporary database and `--canary`; production apply and
formal export publication remain disabled.

The established daily collector is now available through the default
read-only extraction adapter. When explicitly enabled, the existing detail
retry/apply contract can enrich the isolated canary database. The adapter
still never targets the configured production database. Template 1
publication remains outside the canary because formal production artifacts
are still disabled.
