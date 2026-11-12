# Eight-risk local production release

This release starts from the previously bound production commit, retaining its
SQLite/QA/localization contracts, storage lock and recoverable history publisher.
It does not replace production with the older dirty development checkout.

## Production routing

The official data-root entry is `scripts/run_production.ps1`. Daily's existing
`scripts/run_production_daily.ps1` is a compatibility alias. Both resolve the
same `runtime/deployment/active.json`, clean committed release directory, exact
source SHA, explicit Python interpreter and hashes of shared configuration.
Code and the process environment live in stable release/config directories,
not transient ActionTasks or work directories. Old releases remain available
for rollback and are listed in the cleanup-protection manifest.

Examples (run from PowerShell at the data root):

```powershell
./scripts/run_production.ps1 -Command validate
./scripts/run_production.ps1 -Command export-history -Date 2026-11-09
./scripts/run_production.ps1 -Command export -Date 2026-11-09 -Language es
```

`-OutputDirectory` is export-only. It redirects output to an isolated validation
directory; database, Master, snapshots, state and policies remain bound to the
same production data root. Production publication still requires the existing
complete gate, selected using `-ReleaseMode production_release`.

## Weekly binary history

`weekly_raw_catalog_v2` uses reviewed original full catalogs, not old generated
matrices, archives, current state or first-seen dates. Its publication contract
is version 4. January 9 is the owner-confirmed baseline exception; other periods
are Mondays. July 1's source maps to June 29, August 2's source to August 3.
The latest instruction restores 2557704 with the Chinese name 汽水.

All registered sources require an exact SHA-256, positive unique SKU count,
complete observation and absence capability. Newly discovered Monday files
also require a `.weekly-source.json` receipt with contract
`ACTION_WEEKLY_CATALOG_SOURCE_V1`, matching `date`, `sha256`,
`expected_sku_count`, `observation_complete: true`, `qa_state: PASS`,
`commit_status: FULL_COMMIT`, and `authorized_by: human:owner`.
Missing receipt, changed/truncated source, gaps or ambiguous files block export.
Do not generate owner approval automatically from a filename or local boolean.
No UNKNOWN/blank is accepted in this profile's exported date columns.

The existing production storage lock and journaled pair publisher are reused.
Failed publication restores the previous workbook and manifest; unique temporary
files and durable recovery evidence prevent the old fixed-preview race.

## Legacy maintenance and price consistency

The old fixed-period history scripts and historical-Master builder are retained
but their executable write entrypoints are retired. Legacy database repair is
preview-only by default; its CLI cannot apply to live production or an unreviewed
copy. Direct repair-library calls require explicit authorization and reject the
live runtime path. No database repair is executed by this release.

Long-term Master refresh includes finite positive old/new history prices,
existing extrema and current verified price when calculating extrema. It is
idempotent and ignores invalid/nonpositive values. This repairs subsequent
projection generation; it does not silently rewrite existing production facts.

## Verification boundary

Full pytest, CI-safe temporary regression cases, publication fault injection,
config/source tampering checks, immutable backup and restore rehearsal precede
activation. After activation, validation/export use the installed data-root
wrapper. Source-data approval conflicts remain blocked by the original gates.
No scraping, model calls, ProductCollector changes, remote push or old-program
deletion are part of this release. Exact counts, hashes and execution receipts
belong in the local activation report, not this stable contract.
