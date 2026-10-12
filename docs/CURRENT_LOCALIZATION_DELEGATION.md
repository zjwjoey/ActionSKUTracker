# Current localization delegated approval

`CURRENT_OWNER_DELEGATION_V1` uses actor
`service:current-localization-review` and reuses the Registry approval,
immutable patch staging and atomic Apply contracts. Historical grants retain
their original scope and actor.

A current grant binds explicit revision IDs, source and target hashes, a
verified review artifact digest, Owner authorization, expiry, business date,
collection run and finite SKU list. Every approved SKU must remain CURRENT
and have a complete PRESENT Listing observation in that committed QA-PASS,
non-dry-run collection. Existing freshness, QA, open blocker, current
revision, evidence and PRIMARY source checks continue to apply at approval,
patch staging and Apply. The Apply coordinator still checks the current
base commit and old Chinese value.

This adds no default approval, collection, translation, daily schedule or
publication switch. Current execution plans select current revisions only;
historical pending records are retained but excluded from these plans.

The accompanying source-fidelity corrections recognize Spanish decimal
commas before spelled-out measurement units (`1,500 litros` is 1.5 litres),
preserve independently classified model names, and reject invalid physical
state values inside source lists. Body QA and release residual detection
share complete source-bound product/IP spans; they do not whitelist the
individual words or accept a name absent from its own Spanish field. No
release threshold, failed-QA exception or source-content replacement is added.

The current runner validates the review's original target and six-field
source context again under the production lock. It creates independent
review revisions instead of replacing another actor's approval, checks
recoverable backups, and skips fields already applied with matching approved
provenance. Historical work remains outside its finite CURRENT manifest.
