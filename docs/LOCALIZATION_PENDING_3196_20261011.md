# 3,196 SKU localization continuation

The execution scope is frozen from PRIMARY at
`2026-10-10_desc_details_repair_20261010T180308518582Z_c0f6b8cee2de`.
Its 3,196 unique SKUs contain 8,756 outstanding fields: 4,633 blank targets
and 4,123 nonblank targets requiring approval, freshness or source binding
review. These are completion/review counts, not approved Apply counts.

Runtime evidence lives at
`F:/ActionSKUTracker/runtime/reports/localization_pending_scope_20261011/execution/`.
`scope.jsonl` records each field's current value, Spanish source, full source
context, provenance, issue reasons and historical source evidence. Its SHA-256
is bound by `baseline.json`. Unknown source evidence is kept for source audit;
it is never treated as a verified official empty field.

Work uses the existing historical Translation Registry / QA / owner delegation
/ immutable patch / Apply path. It preserves the historical-only boundary of
`HISTORICAL_OWNER_DELEGATION_V1`; CURRENT fields cannot be passed to that grant.
Review, translation and approved-but-unapplied counts remain separate. Candidate
creation never counts as a production Apply. Metadata rebindings are reported
separately from text corrections and missing-field backfills.

## Compact measurement regression

Real specifications for SKUs 3215347, 3224104, 2561124 and 2581866 preserve
their counts, measurements and units in `8×70ml`, `12×50ml`, `30×17cm` and
`38×38cm`. The unit scanner nevertheless rejected them because its broad
Latin-1 identifier boundary included the multiplication sign U+00D7.

The boundary now excludes multiplication/division separators while retaining
the Latin identifier and digit guards. It does not change source facts,
generation rules, dictionaries, protected models or approval policy. The real
source/target pairs and source artifact hashes are in the regression fixture;
tests also reject dropped/changed units and numbers embedded in identifiers.
An independent source comparison supplies KEEP decisions; Guard PASS alone
never supplies semantic approval.

The 30-field specification pilot retains four quantity/sheet-page source
conflicts. Decibel token-spacing/localization and PPP/DPI equivalence remain
separate QA review issues; this fix does not waive them.

## Cubic flow regression

The source-bound specification for pool pump 3004507 is
`2,3 m3/hora | 12 voltios`. Its retained candidate
`2.3 立方米/小时 | 12 伏特` is independently faithful, but the old Guard
counted the ASCII exponent as another quantity and classified cubic metres
as ordinary metres. This is a reproducible QA false rejection, not a reason
to waive source or approval requirements.

The Guard now recognizes cubic units and equivalent Chinese unit names,
removes standalone measurement exponents from numeric quantity comparison,
and preserves the hourly cubic-flow denominator. Model suffix digits remain
literal. New cubic measurements require their own source field; a quantity
in the description cannot authorize adding flow to the specification.
The fixture binds the real source, retained candidate and source artifact
hash. Regression negatives cover area/length substitutions, dropped/changed
rate denominators, numeric changes and cross-field fact imports.

This change does not generate translations, publish dictionaries, change
the source hash contract, deploy the daily runner or approve high-risk name
corrections. Only the existing native Registry / QA / Approval / immutable
Apply flow can publish reviewed targets.

Validation: the real pool-pump case and two numeric/own-field assertions fail
before the change. After the change, the 18 cubic/compact-unit regressions
pass and the full local suite passes 1,325 tests. The source-bound pool-pump
candidate passes preflight QA after the repair; preflight is not an approval
or an actual PRIMARY Apply.

## Source-faithful brand/IP preservation

The native resolver marked typed brand/IP terms as display-omittable in
every field and deleted them after provider translation. Deterministic
planning also deleted them from specifications and prose. Both paths now
apply the no-brand policy to names only, as required by the shared V2
review contract. Other fields retain their own source-supported tokens;
title tokens cannot authorize adding them to a description.

The real pencil-case SKU 3217243 fixture records its full own-field source,
source hashes and rehashed historical CSV evidence. Its regression output
tests token preservation only; it is not a gold translation or approval.
Isolated tests cover the deterministic specification path, provider details,
continued no-brand names and cross-field import prevention. All 84 focused
resolver tests and the 1,330-test local suite pass. The fixture-bound test
module also passes all five tests after evidence binding.

Four additional descriptions were generated through the existing native
Qwen-MT adapter and finite candidate pipeline. Full request/response hashes,
request IDs, usage, prior candidates and source evidence are retained in
runtime reports. Resume reused all four with zero provider calls. Three
remain QA-blocked (including numeric representation and contextual goma
findings); one retains HIGH risk and requires Owner approval. Actual Apply
from these four is zero. They do not reduce the missing-field statistics.
This branch remains local; the change does not deploy the daily runner,
publish terminology or expand the finite approval contract.

## Contextual erasers, scaled counts and bounded technical tokens

Real pencil-case SKU 3217243 lists an isolated `goma` alongside pencils and
a ruler/sharpener. The semantic parser now identifies that own-field list
item as an eraser, without inferring rubber composition. It preserves a
separate `Material: Goma` assertion and does not borrow a title's stationery
context for an ambiguous description. Both QA and provider terminology use
this scoped grammar rule; the formal dictionary remains unchanged. QA
rejects rubber bands, omitted erasers and negated eraser mentions.

Real cable SKU 3009538 uses `50 000 veces`; the generated candidate uses
`5万次`. Numeric QA now compares exact 万-scaled values before explicit count
classifiers using decimal arithmetic. Changed values, model suffixes, bare
fragments and counts borrowed from another field remain protected. This
comparison never rewrites source/target strings or approves durability claims.

Real remote-control car SKU 3212557 exposed an extra substring count of LED
inside Spanish `teledirigido`. Technical multiplicity now counts bounded
Latin identifiers, permits adjoining Chinese labels and retains accent
boundaries. Tests reject omitted/extra actual LED occurrences and distinguish
OLED from LED. A mechanically passing candidate still requires independent
meaning review; a new candidate's 双向操控 does not establish the meaning of
the source's `acción bilateral`.

All fixtures include real own-field sources and rehashed historical evidence;
isolated negatives are explicitly test scenarios, not invented official facts.
The 48 contextual/numeric regressions and 124 combined protected-token tests
pass. The intermediate full suite passes 1,350 tests; final full-suite results
after the bounded-token change are recorded with its Git commit.
The final full local suite passes 1,359 tests after all three repairs.
