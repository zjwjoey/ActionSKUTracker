# Post-repair Chinese field semantics

Status: implemented in the local isolated candidate checkout; not deployed to
daily-run or published to the remote branch by this task.

`localization.fidelity.field_semantic_issues` diagnoses finite own-field
contradictions at the shared `audit_translation` boundary. It never translates
or approves a field. A separate `repair_reviewed_detail_roles` helper generates
a bounded candidate only when the source and target pair counts match. It
preserves occurrence order, duplicate attributes, booleans, measurements and
all unlisted target pairs. Candidates still require independent semantic
review, fact QA, a finite delegated approval grant and immutable Apply.

## Contracts

- Adult dentition is not a denture compatibility claim.
- `Sin conservantes: No` remains `不含防腐剂：否`; neither ingredient presence
  nor its absence can be inferred by changing the question to a positive one.
- Seating positions are not seat capacity; electric heat is not motor power.
- Vegan and vegetarian, anti-allergenic and hypoallergenic remain distinct.
- A drinking-cup noun must not introduce glass composition into a ceramic cup.
- A mass-valued `Contenido` is net content, not volume capacity.
- `Halal` states a status; it does not alone establish a certification claim.
- An incorrect AA in the body cannot be cleared by appending AAA at the end.
- Cross-field material or model conflicts remain source review work.

The name alias table is comparison/display logic, not a published dictionary
baseline. Source-local identity matching recognizes punctuation variants and
Chinese aliases only for identities supplied by the active approved context or
finite reviewed role evidence. It must not delete words from other fields.

Export model extraction does not join an uppercase unit at the end of one line
to a composition percentage on the next line. `500 GSM\n100 % algodón` must
not create a `GSM100` model. Functional counts such as `3-in-1` must not be
treated as a brand's trailing digit.

## Verification boundary

CI_SAFE fixtures use temporary/in-memory values only, with no provider, browser,
production data or filesystem artifacts. Current data evidence, reviewed
candidates, QA outcomes, delegated approval, backup and reconciliation live in
runtime reports and are not committed. A rule PASS is not an exhaustive
free-text semantic PASS. Field error statistics deduplicate by SKU and field;
source ambiguity is separately reported.
