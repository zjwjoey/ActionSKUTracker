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

UI button text and HTML transport residue are rejected using the shared
data-quality classifiers before source selection. Such evidence stays in the
versions list and requires source review, never an official-empty designation.
The reviewed Apply runner independently rejects these values even when an
older audit artifact incorrectly labels them EVIDENCE_AVAILABLE. Regression
coverage includes SKU 2546793's batch-09 button-text source.

`tests/fixtures/historical_spec_review_20261010.json` retains source artifact
references, file and field hashes, SKU context, provider candidates and the
five semantically reviewed targets actually applied in specification batch 10.
Offline CI-safe tests reject corrupted numeric facts, reproduce SKU 3218603's
decimal-dimension error and verify that description changes leave specification
hashes unchanged. These five cases cover specification regression only; they
do not constitute full six-field semantic acceptance or the complete future
daily-run acceptance matrix.

Real specification QA regressions 3221778 and 3221803 permit `ledes` → `LED`
only when `ledes` is present in the target field's Spanish source. A negative
test keeps LED additions blocked when the word appears only in description
context. This changes QA equivalence, not source facts or dictionary approval.
SKU 3224354 covers the English plural spelling `LEDs` under the same
field-source boundary.

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
not approve unreviewed provider candidates. New writes require explicit CODEX
semantic PASS, a nonempty review note and LOW/MEDIUM risk; HIGH risk stays in
the Owner queue. Provider-supplied PASS cannot substitute for semantic review.
Existing equal targets require
approved current provenance and Registry readiness before returning NO_OP;
otherwise they remain METADATA_REVIEW_REQUIRED. Optional source restoration and
validated backup execute under the same RunLock. Production uses the real
runtime directory as projection root so it shares the daily-run lock.

New writes also require an explicit `before` target equal to the current exact
Chinese localization value. A missing baseline or a later Chinese correction
routes to target review instead of overwriting it from an older manifest.
Approved equal-target Resume retains the provenance-backed NO_OP path.

`localization.pipeline.translate_pending_requests` provides an atomic finite
provider-batch checkpoint using the existing TranslationRequest/provider
contract. Completed responses survive interrupted calls and are reused on
Resume; a changed source plan, context, policy or provider/model is rejected.
Every response remains PENDING_SEMANTIC_REVIEW. This helper does not write
Registry, approvals, patches or PRIMARY, and does not infer missing sources.

Real-history regressions retain source artifact hashes for SKU 1325690
(full-width Chinese detail separators and unchanged boolean polarity),
2533753 (Chinese numeric layer count), 2523375 (nonsterile mistranslation with
conflicting source material), and 3214854 (unapproved category synonym).
The parser preserves duplicate detail keys and order across ASCII/full-width
semicolon delimiters. Numeric QA recognizes Chinese digits followed by 层;
lexical words such as 五金 remain outside the numeric context. These changes
fix false flags, never create semantic approval or auto-correct Chinese text.

Real-history daily compatibility tests capture three actually applied and
source-bound fields of SKU 3218603. Isolated temporary Registry databases
verify missing-source NEW, approved reuse, one-field source changes,
HISTORICAL-to-CURRENT reuse, repeated same-day ingestion, and unavailable
sources. Changed facts in these tests are explicitly simulated, not official
source versions or guessed translation standards. Registry ingestion now
preserves missing/None evidence as unavailable instead of coercing it to
official-empty strings; a business display name is never an ES fallback.
Existing source/field hash contracts and immutable historical rows are retained.

Further real-history QA regressions cover SKU 3205379/3206321: `karaoke`
may render as 卡拉OK only when present in the same field's source and every
OK token belongs to that complete phrase. Standalone/extra OK and invented
OK99 models remain blocked. SKU 2529728 retains 竹签 as a bamboo-material
compound; 竹纹塑料签 cannot satisfy the material fact. These aliases leave
semantic approval and source-conflict routing unchanged.

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

Real SKU3209565 exposed a false technical-token failure for `BBQ style`
translated as `烧烤风味`. The lexical equivalence requires that complete
phrase in the field's own Spanish source and the explicit flavour rendering.
It does not waive bare BBQ identifiers, BBQ-120 models, or tokens borrowed
from another field. The fixture retains the original archival evidence and
tests omitted flavour and cross-field/model counterexamples.

SKU3210285 exposes a trusted-brand/unit collision: `3M` in its title is a
brand, not a length. Name QA masks only exact, omitted, field-bound BRAND
spans before unit extraction; actual `3 m`/`3m`, missing brand evidence and
wrong-field brand evidence still fail. SKU3211913 exposes the compound noun
`goma de borrar`: semantic parsing records an eraser product type without
asserting rubber material from that noun. A separate `de goma` or details
material remains protected. Both cases carry real archived source evidence
and counterexample assertions; the published dictionary is unchanged.

The historical reviewed pilot's optional `--rebind-kept-values` mode accepts
independently reviewed KEEP values and uses the existing
`include_noop_rebinds` immutable patch path. Equal targets with valid approved
Registry/PRIMARY provenance remain NO_OP; unapproved equal targets require
the explicit option, exact before value, verified source, low/medium semantic
approval, full QA and delegated approval. Changed KEEP values remain blocked.
This preserves correct Chinese and allows future incremental reuse without
retranslation. Source binding repairs are separate from text backfill counts.

The history source configuration paths now point at the existing read-only
`F:/按日期整理/action表格` archive. Neither those files nor production Master is
modified by audit. Approved historical compatibility projection changes only
paired ES/ZH name, category and specification cells on existing historical
Master rows. Current rows and business history are preserved. Production
checkout changes pre-existing before this task are preserved.
# 2026-10-10: source-bound detergent capsule nouns

Historical SKUs 3217413 and 3217414 exposed Guard false failures: the
semantic seed expected 胶囊 / 洗洁精 even when the field explicitly described
detergent capsules. The existing semantic alias function now accepts
洗涤凝珠 only for complete detergent-capsule phrases in the same field.
洗衣凝珠 additionally requires a laundry marker (color, ropa or colada)
and is excluded when the source mentions dishes or a dishwasher.
Unrelated medicine capsules, bare detergent, and evidence present only in
another field remain blocked. Quantity and model protection is unchanged.
Two real-source fixtures retain archival file and field hashes; negative
tests cover medicine, dishwashing, partial phrases and cross-field leakage.
No runtime dictionary baseline is published by this change.

## 2026-10-10: product noun error detection from actual name16 candidates

Seven source-backed cases now cover marker pens versus bookmarks, powder
brushes versus decorating brushes, eyebrow trimmers versus contour brushes,
and oil ampoules versus hair masks. Complete source phrases seed PRODUCT_TYPE
facts through the existing semantic parser and Guard. Book/browser marker
phrases are excluded in their own field without suppressing a separate pen
phrase. Generic brushes and foot blisters do not acquire cosmetic facts.
Facts stay attached to their source field; description evidence cannot create
a product noun fact in the name. Existing numeric/model protection remains.
The five wrong identity candidates have no approved gold target in fixtures
and remain Owner review; diagnostic Guard failures do not grant approval.
Two source-backed pen examples verify that valid existing renderings survive.
Unrecognized LU/FAB uppercase brand/model tokens remain blocked pending
trusted brand evidence, rather than bypassing protection for NO_BRAND cleanup.
