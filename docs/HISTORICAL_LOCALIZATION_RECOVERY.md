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

The runner `scripts/run_historical_localization_pilot.py` accepts finite,
semantically reviewed corrections for the six canonical text fields. It does
not approve unreviewed provider candidates. Existing equal targets require
approved current provenance and Registry readiness before returning NO_OP;
otherwise they remain METADATA_REVIEW_REQUIRED. Optional source restoration and
validated backup execute under the same RunLock. Production uses the real
runtime directory as projection root so it shares the daily-run lock.

The pilot manifest covers all six fields for each selected SKU; its size is
not the number of approved or applied Chinese fields. Candidate, review,
clone-validation and production-Apply counts are recorded separately in the
runtime reports. QA PASS alone is never semantic approval.
Historical approval reads the exact ES localization fields; a missing source
does not inherit an unverified business-name fallback. Source restoration uses
separate CommitBundle run IDs from Chinese Apply. Each subsequent batch needs
a distinct `--batch-id` and output directory.

The historical runner also reads the actual Chinese localization projection,
not the business-name fallback used for display. A reviewed legacy Chinese
name can be recovered through the normal approval and Apply chain if the
localization is missing; an existing display value does not count as Apply.
Read-only audits distinguish a missing Chinese row (`NO_LOCALIZATION`) from
an existing row without aggregate freshness metadata (`NO_FRESHNESS_STATUS`).
Reconstructed aggregates do not invent an official observation timestamp;
original field dates remain in source evidence.

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
