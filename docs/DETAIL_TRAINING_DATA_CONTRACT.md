# Detail Translation Training Data Contract

The official detail fetch contains Spanish facts only.  It is not a
supervised Chinese training pair and must never be treated as one.

## Input

The offline dataset builder accepts the optional
`data/dictionary/detail_translation_dictionary.csv` (or an explicit path via
`--detail-dictionary`).  Its exact header is:

```text
sku,source_hash,name_es_raw,cat1_es,cat2_es,spec_es_raw,
description_es_raw,description_zh_standard,
details_es_raw,details_zh_standard,review_status,locked,
source_run_id,source_commit_id,updated_at,notes
```

`sku` and `source_hash` are required.  Each non-empty Spanish field must have
its corresponding Chinese field.  Only `HUMAN_REVIEWED`, `LOCKED`,
`SEED_REVIEWED`, or `CAT1_CONFIRMED` rows are ordinarily eligible.  A user may
explicitly authorize `TRAINING_APPROVED` for offline adapter training only;
that status is not recognised by Dictionary Apply, SQLite PRIMARY, promotion,
or exports.  `PENDING`, `FALLBACK_ES`, `NOT_CONFIGURED`, and model-only rows
are rejected.

## Output

`scripts/build_local_qwen_dataset.py` emits single-field `description` and
`details` examples and, when both pass independently, a flat
`description+details` contract example.  Every output value is a string;
numeric facts must be preserved and ordinary Spanish in the target is
rejected.  The manifest records the input hash and detail example counts.

This file is a training source only.  It is deliberately outside the frozen
seven-file production dictionary baseline and cannot be consumed by Dictionary
Apply, SQLite PRIMARY, or an official export.  AI remains disabled by default.

## Current state (2026-09-02)

The current baseline contains no approved detail translation pairs.  Therefore
Round 7 correctly reports `detail_pair_count=0`; this is a safe zero rather
than a silently trained Spanish fallback.  Newly fetched detail pages should
first be human-reviewed into this CSV, then the dataset can be rebuilt.

For convenience, `scripts/build_detail_training_candidates.py` projects one
or more `detail_fetch.jsonl` files into a `PENDING` candidate CSV.  It can
join the current SQLite product `source_hash`; it still leaves Chinese fields
blank and therefore cannot by itself create training examples.
