# Exact fact variance acknowledgements

Local Data Foundation V3 candidate; no production approval or deployment is implied.

`data_quality.historical.variance` implements `FACT_VARIANCE_ACK_V1` through
the existing `CommitBundle` and `ProductionWriter`. `build_ack_bundle` performs
a read-only preview. The writer revalidates inside its existing transaction,
then records the immutable reviewed payload in `run_evidence`. It adds no table.

Each ACK binds one SKU/canonical identity, one target ES field and its typed
current-value digest, exactly two canonical source versions/fields, raw and
normalized values and hashes, normalization versions, original observation
precision, reviewer, review evidence, review base, review time and expiry.
`source_descriptor` reconstructs each source from the sole canonical fact table:
active `product_fact_versions`, otherwise legacy `source_fact_versions`.
`PRIMARY:<table>:<fact_id>` identifies the stored source asset, not a guessed
website URL. A source not yet imported through the trusted fact writer cannot
be acknowledged by pretending that an external filename is a canonical fact.

ACK hashes use typed canonical JSON so `null`, empty string, zero and false
remain distinct. Existing fact and localization hash contracts stay unchanged.
Product-version import `created_at` is never treated as original observation
time; unavailable original observation dates remain `UNKNOWN`.

Only `ACK_SOURCE_VARIANCE` / `KEEP_BOTH` is supported. Human actor validation
reuses the immutable-patch actor boundary. Service actors are not authorized
by an allowlist contained in the submitted payload. Every ACK explicitly grants
no fact write, QA waiver, Chinese approval or Presence authority. Dedicated ACK
and revocation commits reject every business mutation array, even with QA PASS.
Selecting or correcting a fact still requires the normal correction/QA gates.

`match_ack` returns reviewed context only when SKU, field, target, exact A/B
source descriptors, source version frontier and current validation agree.
A newly appended version of either bound field requires fresh review; another
SKU or a source version containing only an unrelated field does not invalidate
the ACK. Expiry, withdrawal, changed source/value/hash/normalization or target,
missing identity and unsupported contracts all fail closed. A new execution
requires a current base head; the original review base remains auditable.

Revocations append another committed evidence record through the same writer;
they never rewrite the original ACK. Identical run Resume returns its original
receipt. A revoked ACK cannot be reactivated by repeating its old payload.
An expired original commit remains historical evidence, but cannot match a new
execution. Mutable data-quality issues are not the ACK authority.

CI-safe regressions use only generated SQLite fixtures for both schema shapes.
This API does not automatically confirm any existing business conflict and
does not enable automatic translation, Apply, cloud operations or scheduling.
