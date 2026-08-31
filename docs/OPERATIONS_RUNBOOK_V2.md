# Operations Runbook V2

Daily data update remains `production-run` (compatible with P7/P8). Export is
on demand and reads the latest committed SQLite facts. Use the validated
PowerShell wrapper with `-ProjectRoot F:\ActionSKUTracker` when source and
production runtime are separate.

Before additive schema migration: create a SQLite Backup API backup, migrate,
run `db-validate-production`, inspect parity and only then use the production
database. Never drop or rewrite product/lifecycle/history tables.
