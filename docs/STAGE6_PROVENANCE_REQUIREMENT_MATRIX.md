# Stage6 provenance requirement matrix

This contract separates current native candidates from historical review artifacts. A missing `provenance_type` is treated as `NATIVE`; it never activates the legacy path.

| field_name | native_required | legacy_available | legacy_safe_substitute | legacy_missing_allowed | blocking_when_missing | reason |
|---|---|---|---|---|---|---|
| provenance_type | Yes: `NATIVE` or explicit native semantics | `LEGACY_ARTIFACT` | None | No | Yes | Legacy use must be explicit and isolated. |
| candidate_id / SKU / field | Yes | Yes | Exact identity from historical review row | No | Yes | Prevents cross-row or cross-field application. |
| historical source text | Native source snapshot | Yes | `historical_source_text` in revalidation evidence | No | Yes | Source text is the primary fact being localized. |
| legacy review artifact identity/hash | Native review evidence binding | Yes | Artifact path, detected artifact kind, SHA-256 and exact record locator | No | Yes | Allows the historical row to be verified against the real persisted artifact. |
| field-level source hash | Yes | Yes | Recomputed `localization_field_source_hash` from exact current source and compared with stored historical field hash | No | Yes | Staleness must be field-scoped. |
| source revalidation | Current snapshot binding | Yes | Exact historical/current source text match plus equal field hash | No | Yes | No fuzzy or semantic source equivalence. |
| reviewed value / decision / date / review model and policy | Yes: parsed value plus evidence ID, decision, date, model version and policy version | Yes, where the legacy artifact records them | Exact recorded value/decision/date and artifact identity | Yes only where absent from that artifact | Yes for source value/decision/evidence; missing optional historical model/policy fields are recorded as absent | The value must be final and traceable to a review artifact; no model or policy version is inferred. |
| Owner decision / note | Current explicit Owner approval | Yes | Uploaded Owner completion file row | No | Yes | ACCEPT is eligible; REJECT and HOLD are blocked. No reviewer identity is inferred. |
| Owner approval artifact/hash | Current signed or completed approval input | Yes | Completed Owner CSV path, SHA-256 and exact SKU/field row | No | Yes | Approval must remain bound to the submitted artifact. |
| Master baseline | Current target snapshot and expected hash | Yes | Exact target value recorded in the completed Owner queue and matched against current Master | No | Yes | A changed target baseline must stop preview. |
| artifact conflict | Native review binding | Yes | Revalidation report status and artifact identity | No | Yes | Conflicting historical artifacts cannot be resolved by the adapter. |
| source consistency | Current deterministic source checks | Yes | Re-run against current six-field Spanish source | No | Yes | Cross-field contradictions remain blocked. |
| contract_hash | Yes | No | None | Yes, only as documented legacy absence | No, if all legacy minimum evidence passes | Old artifacts predate the modern contract hash. Absence is recorded as unknown, never synthesized. |
| guard_policy_hash | Yes | No | None | Yes, only as documented legacy absence | No, if all legacy minimum evidence passes | The historical run's guard policy hash is not present. |
| raw_model_output | Yes | Sometimes | Only the literal historical output if present in source artifact | Yes, when absent in that artifact type | No, if sufficient reviewed evidence exists | Never recreate model output from the final translation. |
| resolver_result | Yes | Sometimes | Only the literal recorded resolver result if present | Yes, when absent in that artifact type | No, if sufficient reviewed evidence exists | Do not infer the resolver from a later decision. |
| model request/response IDs | Yes where native contract specifies them | No | None | Yes, with absence reason | No, if all legacy minimum evidence passes | Do not invent request metadata. |
| modern source snapshot path/hash/run id | Yes | No | Current Master source plus exact revalidation evidence; not represented as a historical snapshot | Yes, with absence reason | No, if all legacy minimum evidence passes | Current source is not mislabeled as the historical source snapshot. |
| candidate value after approval | Yes | Yes | Exact approved reviewed value | No | Yes | Any post-approval candidate change is blocked. |

## Legacy minimum admission

`LEGACY_ARTIFACT` preview is admitted only when identity, historical source, exact source revalidation, field hash, final reviewed value and decision, evidence path/id, Owner ACCEPT, current Master baseline, and no artifact/source conflict are all present and consistent. Missing modern metadata is listed in `legacy_missing_fields` with a specific absence reason. It is not replaced by empty-string hashes, current policy hashes, timestamps, model outputs, source snapshots, or reviewer identities.

## Preview behavior

The adapter only changes provenance validation for explicit `LEGACY_ARTIFACT` records. Native candidates continue to require modern fields and fail closed when any are absent. Rejected and held records never enter the eligible preview set. This module provides read-only preview decisions; it has no production writer.
