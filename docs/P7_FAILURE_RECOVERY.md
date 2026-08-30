# P7 Failure Recovery Matrix

| Failure | Safe action | Unsafe action | Expected state |
|---|---|---|---|
| Duplicate trigger | Wait for active run | Delete live lock | BLOCKED |
| Stale lock | Verify PID/audit then rerun | Blind deletion | RECOVERY_REQUIRED/next run |
| Collection/QA failure | Stop and inspect evidence | Commit partial observation | FAILED/BLOCKED |
| DB integrity/FK failure | Stop; restore through DB procedure | Retry blindly | FAILED |
| Export failure after commit | `sync-exports` / resume | Roll back product DB | DEGRADED |
| Image failure | `image-sync` / resume | Roll back product DB | DEGRADED |
| Knowledge/AI failure | Rebuild queue later | Put candidate in export | DEGRADED or isolated |
