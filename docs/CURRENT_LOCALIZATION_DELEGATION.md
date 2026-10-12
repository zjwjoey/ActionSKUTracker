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
