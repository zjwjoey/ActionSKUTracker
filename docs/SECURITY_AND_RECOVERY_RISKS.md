# Security and recovery boundaries

## Enforced

Backups consume explicit P1-verified artifacts and exact selections. Raw SQLite
headers cannot be disguised as ordinary assets. CORE projection relations are
checked again at execution and restore. Source aliases and reparse points are
blocked. Unknown files, existing restore targets, unknown job locks and replaced
repositories stop operations. Production/cloud enablement is separate from code
delivery; no automatic deployment, schema change, maintenance or cloud deletion
is installed.

Credential values are absent from command arguments, ordinary configuration,
receipts and error messages. Child environments are allowlisted. Private session
directories retain local connection/cache artifacts under restrictive ACLs;
they should not be copied into public reports. The local acceptance repository
password is an encrypted Windows DPAPI blob, accessible only to the same user.
Operators must arrange their own recoverable password custody before production.

Image/current source bindings use SKU, source version, content identity and
portable locations. Missing files do not alter official source facts. Existing
P1/P2 APIs and business database schemas remain compatible.

## Explicit limitations

- New V3 OSS testing is BLOCKED by expired STS and the user's choice to defer
  refresh. Previous PoC success does not certify the new full implementation.
  COS remains interface/unit-test only until its own real acceptance.
- Observed object bytes and planning limits do not equal billed traffic. Exact
  upload/download/billing are UNKNOWN where the CLI cannot measure them. Strict
  metered cloud budgets fail closed unless estimates are explicitly accepted.
- Sealed JSON is application immutability/corruption detection, not a digital
  signature or protection from a malicious administrator who can replace all
  evidence. External approval files are attributable operator assertions, not
  automatically authenticated human identities. Restrict access and preserve
  approval provenance independently.
- Retention decisions, expiration and physical repository maintenance are
  separate. Snapshots are pinned, unknown responsibilities remain retained,
  and this release exposes retention review without destructive maintenance.
- A process crash after an uncertain snapshot write stops instead of retrying
  blindly. Dead job locks require exact explicit reconciliation; production
  writer locks are never reclaimed by this module. Failed restore directories
  remain for inspection; retry restores into another new root.
- Cancellation is cooperative between stage/copy operations; an active Kopia
  command is bounded by the operation deadline. Independent SQLite validation
  and local hashing may finish their current bounded unit before noticing a
  cancellation. No promise of immediate asynchronous termination is made.
- Local filesystem support does not certify every NAS/USB implementation.
  Disconnects, permission errors and unavailable capacity fail explicitly. The
  chosen storage must support required atomic/exclusive filesystem operations.
- The cleanup module blocks cross-volume quarantine; use its verified archive
  copy path before a separately approved source disposition. Interrupted
  mutation intents require explicit reconciliation, never blind repeated purge.

## Production protection evidence

The external report records exact protected paths and the observation window.
Small selected configuration/dictionary/projection/manifest files are hashed;
PRIMARY/WAL/SHM are checked by file identity, size and modification time without
opening the business database. This does not claim a new full database payload
audit or visibility into every concurrently modified external asset.
