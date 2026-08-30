# P8 Operations / Control Center Architecture

P8 is a read model over SQLite tables and filesystem reports, not a second product database. `OperationsService` exposes status, runs, run detail, quality, export/image/Knowledge summaries and health. The optional server binds only to `127.0.0.1` by default and uses stdlib HTTP; it has no login or public-network mode.
