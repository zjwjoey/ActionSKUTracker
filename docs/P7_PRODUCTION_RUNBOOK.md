# P7 Production Runbook

1. Run `python -m action_tracker production-run --date YYYY-MM-DD` from the project directory.
2. Inspect `runtime/reports/daily/<date>/<run_id>/summary.md`, `steps.json` and `errors.json`.
3. Use `--resume` only after a persisted run state exists; dependencies prevent unsafe `--from-step` jumps.
4. Export failure is recovered with existing `sync-exports`; image failure with existing `image-sync`.
5. Integrity/FK failure or incomplete collection is a stop condition. Do not manually edit SQLite or Master.

Task Scheduler should call the PowerShell wrapper from the configured working directory and use the exit code to distinguish success, degraded and failure. The schedule time is intentionally configured by the operator.
