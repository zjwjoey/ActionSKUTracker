from __future__ import annotations

from pathlib import Path
import uuid
from datetime import datetime
from typing import Any

from ..database.integration import database_path
from ..database.production import validate_production_database
from ..services.gitutil import git_commit_info
from .backup import backup_sqlite
from .contracts import StepResult
from .runner import ProductionRunner


def run_production(cfg: dict[str, Any], *, business_date: str, resume: bool = False, from_step: str | None = None,
                   dry_run: bool = False, no_network: bool = False) -> dict[str, Any]:
    root = Path(cfg["project_root"]) / "runtime" / "reports" / "daily"
    db = database_path(cfg)
    report_root = Path(cfg["project_root"]) / "runtime" / "reports" / "daily"
    run_id = None
    if resume:
        existing = sorted((report_root / business_date).glob("*/state.json"), key=lambda p: p.stat().st_mtime, reverse=True) if (report_root / business_date).exists() else []
        if existing:
            run_id = existing[0].parent.name
    run_id = run_id or f"{business_date}_{datetime.now().strftime('%H%M%S')}_{uuid.uuid4().hex[:6]}"
    def collect_existing_chain() -> StepResult:
        if no_network:
            return StepResult("BLOCKED", {"reason": "NETWORK_DISABLED"})
        # Reuse the established collector/QA/SQLite writer as one atomic
        # business chain. Operations wraps it; it never creates a second
        # crawler or product writer.
        from ..orchestrator.daily import run_daily
        result = run_daily(cfg, dry_run=dry_run, fetch_details=True)
        qa = result.get("qa") or {}
        commit_status = str(result.get("commit_status") or "")
        if not bool(qa.get("passed")) and not dry_run:
            return StepResult("BLOCKED", {"delegated_run_id": result.get("run_id"), "commit_status": commit_status, "qa": qa})
        return StepResult("SUCCESS", {"delegated_run_id": result.get("run_id"), "commit_status": commit_status, "qa": qa})

    steps: dict[str, Any] = {
        "PREFLIGHT": lambda: StepResult("SUCCESS", {"database": validate_production_database(db), "code_version": git_commit_info()}),
        "BACKUP": lambda: StepResult("SUCCESS", backup_sqlite(db, Path(cfg["paths"]["backups"]) / business_date / f"{run_id}.sqlite3", run_id=run_id, code_version=git_commit_info())),
        "COLLECTION": collect_existing_chain,
        "QA": lambda: StepResult("SKIPPED", {"reason": "included in delegated daily-run chain"}),
        "DB_COMMIT": lambda: StepResult("SKIPPED", {"reason": "included in delegated daily-run chain"}),
        "EXPORT": lambda: StepResult("SKIPPED", {"reason": "compatibility export handled by delegated chain"}),
        "IMAGE": lambda: StepResult("SKIPPED", {"reason": "explicit image-sync remains separate"}),
        "KNOWLEDGE": lambda: StepResult("SKIPPED", {"reason": "queue-only knowledge stage remains gated"}),
        "AI": lambda: StepResult("SKIPPED", {"reason": "AI_DISABLED"}),
        "AUTO_APPROVAL": lambda: StepResult("SKIPPED", {"reason": "AUTO_APPROVAL_DISABLED"}),
        "REVIEW": lambda: StepResult("SKIPPED", {"reason": "review queue is read-only aggregation"}),
        "REPORT": lambda: StepResult("SUCCESS"),
    }
    runner = ProductionRunner(root=root, business_date=business_date, run_id=run_id, steps=steps, lock_dir=Path(cfg["paths"]["state"]))
    # Backup artifact needs the generated run id in its manifest; runner's
    # backup step is still safe if the destination is fixed per business date.
    return runner.run(resume=resume, from_step=from_step)
