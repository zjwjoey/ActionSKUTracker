from __future__ import annotations

from pathlib import Path
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
    run_id = None
    steps: dict[str, Any] = {
        "PREFLIGHT": lambda: StepResult("SUCCESS", {"database": validate_production_database(db), "code_version": git_commit_info()}),
        "BACKUP": lambda: StepResult("SUCCESS", backup_sqlite(db, Path(cfg["paths"]["backups"]) / business_date / "production.sqlite3", run_id="pending", code_version=git_commit_info())),
        "COLLECTION": lambda: StepResult("BLOCKED", {"reason": "NETWORK_DISABLED"}) if no_network else StepResult("SKIPPED", {"reason": "use existing daily-run entry for live collection"}),
        "QA": lambda: StepResult("SKIPPED", {"reason": "collection delegated to daily-run"}),
        "DB_COMMIT": lambda: StepResult("SKIPPED", {"reason": "collection delegated to daily-run"}),
        "EXPORT": lambda: StepResult("SKIPPED", {"reason": "collection delegated to daily-run"}),
        "IMAGE": lambda: StepResult("SKIPPED", {"reason": "explicit image-sync remains separate"}),
        "KNOWLEDGE": lambda: StepResult("SKIPPED", {"reason": "queue-only knowledge stage remains gated"}),
        "AI": lambda: StepResult("SKIPPED", {"reason": "AI_DISABLED"}),
        "AUTO_APPROVAL": lambda: StepResult("SKIPPED", {"reason": "AUTO_APPROVAL_DISABLED"}),
        "REVIEW": lambda: StepResult("SKIPPED", {"reason": "review queue is read-only aggregation"}),
        "REPORT": lambda: StepResult("SUCCESS"),
    }
    runner = ProductionRunner(root=root, business_date=business_date, steps=steps, lock_dir=Path(cfg["paths"]["state"]))
    # Backup artifact needs the generated run id in its manifest; runner's
    # backup step is still safe if the destination is fixed per business date.
    return runner.run(resume=resume, from_step=from_step)
