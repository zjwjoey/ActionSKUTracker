# Applied field export precedence

The local repair candidate preserves a CURRENT PRIMARY Chinese field when its
field provenance exactly matches the current value and own Spanish field hash,
has APPROVED, VERIFIED, HUMAN_REVIEWED or HUMAN_APPROVED review status, and has
both an approver and an applied commit. Row approval and aggregate hashes do
not establish this precedence.

Such a value takes priority over legacy export CSV overrides, deterministic
source-fact repairs and category dictionary mappings. This prevents a complete
approved description from being truncated by an older override, and prevents
correct phone-accessory categories from reverting to an older mapping. The
confirmed product-brand display policy still applies to names. Unit prices
retain their existing normalization path.

Missing/stale/mismatched provenance uses the existing repair behavior. Source
absence still clears the displayed Chinese field. This change does not grant
approval, change a dictionary or waive semantic QA, source notes, numeric QA,
release gates, Apply or deployment requirements. It does not update PRIMARY.

Public audit regressions include 3207419 (complete slipper description),
3213063 (two fast-charge ports plus two USB-C ports), and 3207984 (phone-case
accessories mapping). Test approval metadata is synthetic and grants no real
approval. Local implementation and tests do not imply production activation.
