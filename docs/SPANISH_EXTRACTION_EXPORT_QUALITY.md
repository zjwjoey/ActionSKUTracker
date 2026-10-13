# Spanish extraction and export quality

Local implementation on `fix/spanish-extraction-export-quality-20261012`.
This is not a deployment of the production daily-run checkout.

## Collection

The detail parser reads each specification row's direct cells, retaining the
whole official label and value separately in `_raw_detail_rows`. Unusual row
shapes remain literal rather than disappearing. Raw flattened evidence is also
retained. A colon already present at the end of a label is not duplicated.

The shared formatter repairs only the observed damaged compound-label shape
`Material: del mango: Metal` to `Material del mango: Metal`, for a finite set of
official label prefixes and the grammatical connectors `de` / `del`. Arbitrary
value colons, prose, numbers, units, and conflicting rows remain unchanged.
Official duplicate rows remain in collected facts; they are not source errors
merely because their keys repeat.

## Spanish export

`build_es_rows` uses the same compound-label formatter. It suppresses only
exactly repeated explicit key/value strings in the display. It never chooses
between different values or infers a missing unit. PRIMARY and Master remain
unchanged. This mechanical display projection is also used by Template 1.

The existing finite official-label delimiter contract additionally recognizes
verified nutritional labels in legacy rows such as `Sin lactosa No` and
`Carbohidratos 55 g`. This supplies punctuation, never new values. Newly
observed website differences (for example a package content/ingredient value)
require a separate normal source update and cannot enter a display repair.

Discount remarks derive from the validated current/original price pair for both
PRIMARY and Master; the result is explicitly labelled `Descuento calculado`.
Missing descriptions are described as absent in the source record, not as an
unfinished extraction task.

Known ambiguous source words `inoloras` and `Sustancia: Válido` remain literal.
Collection adds `_source_quality_findings`; export adds an explicit review note.
No inferred Spanish replacement or QA exemption is created. Source findings
are distinct from display repairs and missing fields.

The workbook's atomic manifest contains `spanish_projection`: source-bound
before/after events, rule version, unique repaired field/SKU counts and unique
unresolved source field/SKU counts. A successful workbook export does not mean
all official source wording has passed semantic review.

## Verification

For current source-version differences, the finite browser evidence runner
`scripts/run_official_detail_evidence_apply.py` validates exact official URL,
article ID, current source hash and expected committed head. It defaults to a
preview. Explicit commit holds the daily-run lock, verifies a restorable SQLite
backup, invokes the existing `apply_detail_corrections` API and synchronizes
Master/State. Only the evidenced Spanish details are submitted. Existing Chinese
text is retained; its freshness may become STALE under the existing source-change
contract. Official cross-field disagreements remain recorded, not reconciled
by guessing. The runner does not translate or invoke Qwen.

Targeted tests cover compound boundaries, arbitrary value colons, preservation
of conflicting/repeated source facts, exact display deduplication, idempotence,
literal corrupt source values, discount parity and input immutability. Full
pytest and real 2026-10-12 re-export/audit results are recorded under runtime;
runtime reports are not committed as code.
