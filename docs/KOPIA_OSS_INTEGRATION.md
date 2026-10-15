# OSS integration and evidence boundaries

The prior real PoC used Kopia 0.23.1 and completed repository creation, synthetic
snapshot upload, disconnect/reconnect, independent restore and matching SHA-256.
Its dedicated Bucket was `actionskutracker-sqlite261015`, region `cn-hongkong`,
S3 endpoint `https://s3.oss-cn-hongkong.aliyuncs.com`. TLS validation remained on.
The successful run is independently retained under the user's StorageAuditOutputs
directory. The earlier GitHub PoC contained only a read-only connection seam;
that success must not be described as the old code's full backup capability.

V3 adds the actual filesystem/S3 backend, pinned snapshots, bounded commands,
isolated configuration, no automatic maintenance/actions, identity validation,
100% file verification and restore. OSS, COS and generic S3 share this protocol;
provider compatibility requires real per-provider acceptance, not just a mock.

## Current new cloud gate

The saved STS credentials expired at 2026-10-15 11:01:20 Asia/Shanghai. The user
explicitly chose not to refresh credentials in this turn. Therefore **the new
V3 OSS Gate 3 is BLOCKED**, with no new cloud repository or uploaded business
data. Earlier PoC ROUNDTRIP_PASS remains valid historical evidence; it is not
relabelled as a V3 implementation test. COS is not live-tested.

The prepared new synthetic prefix is
`actionskutracker-kopia-poc/storage-v3-20261015-a61c2e/`. It requires a fresh STS,
fresh empty-prefix observation, scoped creation grant and only the permission
to delete newly created `s*` session markers in that exact prefix. Kopia creates
and removes its own session markers while committing metadata. Existing PoC
repositories and their objects are preserved. No cloud cleanup is performed.

Only a backend with explicit `purpose=synthetic_test` may use a new constrained
`storage-v3-*` test prefix under the PoC namespace. Operations additionally check
the deterministic non-business fixture recipes and total payload <=1 MiB.
Actual business data is rejected by this test-repository purpose. Formal future
backups require a separate approved business prefix and first-upload approval.

## Measurement and costs

`blob list` sizes are observable repository object bytes, not network transfer
or billing. CLI upload limits and conservative logical-byte planning budgets
bound operations but cannot precisely measure provider-billed requests/retries.
Actual transfer/billing remain UNKNOWN where unobservable. Strict metered cloud
budgets block unless the operator explicitly allows estimated planning budgets.
No Bucket permissions, policies, lifecycle settings or paid resources are
created or changed by the adapter.
