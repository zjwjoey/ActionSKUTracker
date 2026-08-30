# P7/P8 Production Operations Gap Analysis — 2026-08-30

Baseline: `main` `83e86ba`; P0–P6 regression 309 passed; SQLite PRIMARY integrity/FK PASS. Existing daily collector, SQLite writer, export sync, image sync, Knowledge queue, RunLock and logging were reused; no crawler or second product writer was introduced.

| Area | Existing capability | Gap/action | Severity |
|---|---|---|---|
| P7 scheduler | Existing `run_daily.ps1` wrapper | Added single `production-run` entry and documented Task Scheduler wrapper contract | CLOSED |
| P7 state/steps | Daily snapshots and run records existed | Added persisted step state, deterministic states, exit codes and reports | CLOSED |
| P7 lock | `RunLock` existed | Reused it for production runner and duplicate-trigger gate | CLOSED |
| P7 backup | DB writer existed; no operations wrapper | Added SQLite Backup API wrapper + manifest details | CLOSED |
| P7 retry/resume | Detail/export services were individually retryable | Added step-level resume; successful DB commit is never repeated | CLOSED |
| P7 reports | Run reports existed | Added summary/steps/errors Markdown+JSON report set | CLOSED |
| P8 read model | `status`, `db-status`, image/QA commands existed | Added `OperationsService` aggregating actual SQLite/runtime data | CLOSED |
| P8 CLI | No unified ops namespace | Added `ops status/health/runs/run/serve` | CLOSED |
| P8 dashboard | No control center | Added minimal stdlib localhost-only JSON dashboard service; no React/Vue/Node | CLOSED |
| P8 safe actions | No common action contract | Added confirmation-only action gateway returning existing CLI commands; no direct product updates | CLOSED |
| P8 health | DB validation existed | Added DB/FK/disk/writable/lock/export health aggregation | CLOSED |
| Failure injection | Existing P0–P6 tests | Added duplicate lock, collection stop, export degraded/resume and read-parity fixture tests | CLOSED |

P7/P8 code is fixture-safe and does not enable a real Scheduler automatically. A real full production run and three-run Scheduler shadow period remain operational rollout steps, not claims of this code-only acceptance.
