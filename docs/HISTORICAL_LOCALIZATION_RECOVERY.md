# Historical localization recovery

## Implemented on the isolated development branch

`python -m action_tracker.localization.history_audit` performs a read-only
inventory of all non-CURRENT products using the existing repository, historical
workbook readers, canonical source hashes and Fact QA.

Required arguments: `--database`, `--master`, `--snapshots`, `--output`.
Optional arguments: `--backup`, `--history-sources`, `--verified-archive`.

The backup is a SQLite Backup API snapshot. A separate restored copy must pass
integrity, foreign keys and logical hashes of every table. Existing backups
cannot be overwritten.

Evidence priorities are official fact versions and QA-verified snapshots,
SKU-bound historical source workbooks, then Long Term Master. Different source
versions remain in the evidence report. Conflicting values at the selected
priority require review. Chinese or replacement characters in a purported
Spanish field require source review. Blank archive cells do not prove an empty
official field. Master provides no description/details evidence.

The optional verified archive requires a confirmed historical/official review
status plus an exact Spanish Action URL/SKU match. Its verification date is not
treated as the original collection time. Existing Chinese is a reuse candidate
only when its paired Spanish field equals the selected source.

Outputs include all six canonical fields per historical SKU, source versions,
file hashes, previous Chinese, archived Chinese, QA findings, binding diagnostics,
unavailable/conflicting sources, STALE rows and a diverse 100-SKU pilot manifest.
Cat3 and selling points are not supported standalone fields in the current
localization contract and are not invented or flattened into other fields.

Fact QA findings are candidates for review, not confirmed semantic errors.
Fact QA PASS is not semantic approval. This command creates no translation
tasks, revisions, approvals, patches, Apply commits or lifecycle events.

## Safety fix

The existing CommitBundle category backlog writer now verifies CURRENT status
before enqueueing a category gap. Historical source restoration does not create
today's category backlog entries. A temporary-database regression verifies
unchanged product facts, zero observations/prices/events and retry idempotency.

## Explicit delegated approval

Owner authorization `codex:user-reply:call_cf3d61a3ddbd4005967b91e176358501`
permits the auditable `HISTORICAL_OWNER_DELEGATION_V1` contract. The exact
`service:historical-localization-review` identity is retained in approval events
and field provenance. It is never represented as a human.

Each finite grant binds revision IDs, SKU, field, aggregate source and target
hashes, reviewed evidence artifact SHA, authorization evidence and UTC expiry.
Approval, patch staging and Apply independently verify the grant, current
historical scope, current revision, freshness, QA, canonical QA, open blockers
and current PRIMARY source. Unrelated service calls retain the human-only gate.
Delegated Apply does not support unit price modifications.

The runner `scripts/run_historical_localization_pilot.py` currently accepts
semantically reviewed name corrections only. Existing equal targets require
approved current provenance and Registry readiness before returning NO_OP;
otherwise they remain METADATA_REVIEW_REQUIRED. Optional source restoration and
validated backup execute under the same RunLock. Production uses the real
runtime directory as projection root so it shares the daily-run lock.

The 100-SKU manifest covers 600 fields; it does not mean 600 Chinese fields have
passed semantic review. The isolated pilot restored 286 Spanish fields on 68
SKU and applied 10 reviewed Chinese names. Four reviewed names were retained,
two require review. Full regression passed 875 tests before final runner edits.
Current SKU, localization, prices, events, observations and lifecycle hashes
matched the original backup. Repeat execution and final production acceptance
must be recorded in runtime reports.

The `stale.jsonl` report lists all fields belonging to aggregate-STALE SKU;
its row count is not a count of individually stale fields. Field bindings and
freshness remain explicit in each row for individual investigation.

The history source configuration paths now point at the existing read-only
`F:/按日期整理/action表格` archive. Neither those files nor production Master is
modified by audit. Approved historical compatibility projection changes only
paired ES/ZH name, category and specification cells on existing historical
Master rows. Current rows and business history are preserved. Production
checkout changes pre-existing before this task are preserved.
