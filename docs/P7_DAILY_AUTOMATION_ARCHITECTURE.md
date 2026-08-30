# P7 Daily Automation Architecture

Windows Task Scheduler invokes one command: `python -m action_tracker production-run`. The runner owns lock, preflight, SQLite Backup API backup, collection/QA/commit handoff, export/image/Knowledge stages, AI/approval gates, review aggregation and daily reports. Existing collector and SQLite writer remain the only business writers. Exit codes are 0 success, 10 degraded, 20 blocked, 30 failed, 40 recovery required and 50 configuration error. Business dates use the existing Madrid timezone contract.
