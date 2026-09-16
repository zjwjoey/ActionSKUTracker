# Translation Runtime V1 — implementation contract

This branch implements the first executable slice of the translation-system
design without enabling production writes:

* `localization/providers/qwen_mt.py` is a dedicated `qwen-mt-flash` adapter.
  It sends one user message, uses `translation_options` for terms/TM/domain,
  supports Alibaba native and OpenAI-compatible endpoints, retries only
  network/429/5xx failures, and reads `DASHSCOPE_API_KEY` from the environment.
* `localization/protection/tokens.py` protects URLs, SKUs, model identifiers,
  numbers and units. A response with a missing or changed placeholder fails
  closed instead of entering a candidate queue.
* `localization/qa.py` is a deterministic candidate guard for required fields,
  Spanish/HTML residue, categories and numeric preservation.
* `localization/registry` and the additive SQLite schema store immutable source
  versions, field units, model revisions, provider calls, QA findings, TM and
  terminology. These records are not a production Apply path.
* `localization/hashes.py` documents the compatible `SOURCE_HASH_V1` contract
  and exposes a new deterministic V2 helper without rewriting historical V1
  hashes.

The configuration remains `enabled: false`. A real API call requires an
explicit operator change and an environment key; no credential is committed.
The existing Daily/Export/Master paths are unchanged in this phase.

For an explicit shadow registration run use:

```text
python -m action_tracker localization-registry-ingest \
  --run-id <official-observation-run> \
  --observed-at <ISO timestamp>
```

This registers source versions and creates field-level translation queue
items only. It does not apply a Chinese value, update Master, or promote a
model result.

TM/Gold/Patch migration is likewise preview-only:

```text
python -m action_tracker localization-migration-preview \
  --input <tm.csv> --input <gold.xlsx> --input <patches.csv> \
  --output <preview-directory>
```

The command emits `eligible.csv`, `rejected.csv`, `conflicts.csv` and a
hash-bound `manifest.json`. Missing source hashes, missing identity/targets,
unapproved statuses and multiple approved targets for the same
`(SKU, field, source_hash)` are excluded. It always declares
`production_writes=false`, `sqlite_writes=false`, `master_writes=false` and
`dictionary_writes=false`.
