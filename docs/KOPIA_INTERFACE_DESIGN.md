# Kopia optional connection PoC

Based on main 14b3747 (P1/P2 merged). P2.1 cleanup_evidence and its contract
were reviewed in their separate unmerged branch; no P2.1 merge or cleanup rule
change is included here. This branch is not a production deployment.

`operations.kopia_connectivity.ConnectionConfig.from_settings` reads
`storage_foundation.kopia`. `enabled` must be a YAML boolean and defaults to
false. There is no daily-run call site. Existing Verified Backup, restore
rehearsal, retention and Storage Lifecycle APIs are reused without modification;
this adapter uses their StorageError contract and adds no business schema.

Config: provider (`oss`/`cos`), CLI path, bucket, HTTPS endpoint, region.
Per-provider environment variables override non-secret bucket/endpoint/region:
`ACTION_KOPIA_OSS_BUCKET`, `ACTION_KOPIA_OSS_ENDPOINT`,
`ACTION_KOPIA_OSS_REGION` (replace OSS with COS for Tencent).
Secrets are read only from `ACTION_KOPIA_{OSS|COS}_ACCESS_KEY`, `SECRET_KEY`,
`PASSWORD`, optionally `SESSION_TOKEN`. Temporary STS credentials require their
session token and must remain valid through the probe. Do not paste secrets into
chat, YAML, CLI arguments, reports or Git.

`cli_environment` checks executable/version and S3 CLI flags; `tls_probe`
validates DNS/network/TLS with the system trust store. Neither proves cloud
authentication. `connection_test` returns independent stage evidence and a
closed error category; missing config returns BLOCKED without a cloud CLI call.
Disabled returns before any CLI/network operation.

An explicitly requested `connect=True` can connect **an existing repository**
read-only under exactly `actionskutracker-kopia-poc/`, then disconnect. A new
exclusive local workspace is required. Windows ACL inheritance is removed and
access limited to current user/SYSTEM; POSIX mode is 0700. Credentials travel in
the child environment, not process arguments. Password persistence and repository
logging, file logging and content logging are disabled. Kopia's local connection config can still contain S3 keys;
keep that private workspace out of Git and ordinary reports. Captured diagnostics
are categorized without echoing raw text; the log directory is also overridden
into this private workspace defensively. Never share that directory as a public report.

Successful connection proves authenticated repository **read** access, not Put
permission. AUTH_OK/BUCKET_ACCESS_OK are recorded only after successful encrypted
repository connection; failures never infer earlier successes. ROUNDTRIP_PASS
remains NOT_TESTED. This PoC implements no repository creation, snapshot, restore,
maintenance, deletion, scheduling or cloud management. With no dedicated bucket
or write authorization, the requested conditional roundtrip is BLOCKED. A later
authorized synthetic-only validation must create an independent prefix, capture
object inventory, reconnect/restore and compare SHA-256; this is not implemented
or claimed by the current seam.

Operator preflight (source environment, not production launcher):

```powershell
$env:PYTHONPATH='<isolated-checkout>/src'
python scripts/kopia_connectivity_poc.py --enable-probe --cli-path '<isolated-tools>/kopia.exe' --output '<new-report-directory>'
```

The explicit preflight flag enables only the in-memory probe. YAML remains false.
The script never calls connect/create or uploads any supplied path. Reports are
exclusive new files outside the source checkout. Unit tests mock all network/CLI
interactions and are CI_SAFE.

## Compatibility evidence and limits

- [Kopia 0.23.1 official release](https://github.com/kopia/kopia/releases/tag/v0.23.1).
- [Kopia S3 CLI flags](https://github.com/kopia/kopia/blob/v0.23.1/cli/storage_s3.go)
  expose endpoint/region/prefix/session token; no addressing-style switch.
- [Kopia S3 implementation](https://github.com/kopia/kopia/blob/v0.23.1/repo/blob/s3/s3_storage.go)
  uses MinIO with SignatureV4 and default bucket lookup. No Kopia patches are made.
- [OSS S3 compatibility](https://www.alibabacloud.com/help/en/oss/developer-reference/compatibility-with-amazon-s3)
  requires virtual-hosted requests.
- [OSS AWS SDK configuration](https://www.alibabacloud.com/help/en/oss/developer-reference/use-aws-sdks-to-access-oss)
  uses `s3.oss-{region}.aliyuncs.com`; account/region restrictions and possible
  custom-domain requirements must be checked for the actual dedicated bucket.
- [COS AWS S3 SDK guide](https://cloud.tencent.com/document/product/436/37421).
  Use actual COS region and bucket name including APPID; verify addressing and
  signing with the installed Kopia version rather than assuming compatibility.

Official S3 support is not an end-to-end Kopia result. Neither provider is ranked
without authenticated tests on actual buckets. TLS verification is never disabled.
