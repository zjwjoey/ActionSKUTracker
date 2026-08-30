from __future__ import annotations

from pathlib import Path
import json
import os
import uuid
from datetime import datetime
from typing import Any

from ..database.integration import database_path
from ..database.production import validate_production_database
from ..services.gitutil import git_commit_info
from .backup import backup_sqlite
from .contracts import StepResult
from .preflight import production_preflight
from .runner import ProductionRunner


def run_production(cfg: dict[str, Any], *, business_date: str, resume: bool = False, from_step: str | None = None,
                   dry_run: bool = False, no_network: bool = False, run_id: str | None = None) -> dict[str, Any]:
    root = Path(cfg["project_root"]) / "runtime" / "reports" / "daily"
    db = database_path(cfg)
    report_root = Path(cfg["project_root"]) / "runtime" / "reports" / "daily"
    auto_selected = False
    if run_id and not resume:
        raise ValueError("RUN_ID_ONLY_ALLOWED_WITH_RESUME")
    if resume:
        run_id, auto_selected = _resolve_resume_run(report_root, business_date, run_id)
    run_id = run_id or f"{business_date}_{datetime.now().strftime('%H%M%S')}_{uuid.uuid4().hex[:6]}"
    delegated: dict[str, Any] = {}
    if resume:
        prior_state = report_root / business_date / str(run_id) / "state.json"
        if prior_state.exists():
            try:
                prior = json.loads(prior_state.read_text(encoding="utf-8"))
                details = prior.get("steps", {}).get("COLLECTION", {}).get("details", {})
                if details.get("delegated_run_id"):
                    delegated["result"] = {"run_id": details["delegated_run_id"], "commit_status": details.get("commit_status", "")}
            except (OSError, json.JSONDecodeError):
                pass
    def collect_existing_chain() -> StepResult:
        if no_network:
            return StepResult("BLOCKED", {"reason": "NETWORK_DISABLED"})
        # Reuse the established collector/QA/SQLite writer as one atomic
        # business chain. Operations wraps it; it never creates a second
        # crawler or product writer.
        from ..orchestrator.daily import run_daily
        result = run_daily(cfg, dry_run=dry_run, fetch_details=True, _skip_lock=True)
        delegated["result"] = result
        qa = result.get("qa") or {}
        commit_status = str(result.get("commit_status") or "")
        if not bool(qa.get("passed")) and not dry_run:
            return StepResult("BLOCKED", {"delegated_run_id": result.get("run_id"), "commit_status": commit_status, "qa": qa})
        # A passed QA report is not sufficient evidence that the formal write
        # completed.  The delegated daily chain can fail while building the
        # SQLite bundle, projecting compatibility files, or replacing state.
        # Only these two statuses mean the chain reached its commit boundary;
        # export-pending is deliberately allowed through so EXPORT can make
        # the outer run DEGRADED and retain a retryable error.
        if not dry_run and commit_status not in {"FULL_COMMIT", "DB_COMMITTED_EXPORT_PENDING"}:
            return StepResult("BLOCKED", {
                "delegated_run_id": result.get("run_id"),
                "commit_status": commit_status,
                "qa": qa,
                "reason": "FORMAL_COMMIT_NOT_CONFIRMED",
            }, error_code="FORMAL_COMMIT_NOT_CONFIRMED")
        return StepResult("SUCCESS", {"delegated": True, "delegated_run_id": result.get("run_id"), "commit_status": commit_status, "qa": qa})

    def delegated_run_id() -> str | None:
        result = delegated.get("result") or {}
        return str(result.get("run_id") or "") or None

    def image_step() -> StepResult:
        if not bool((cfg.get("run") or {}).get("image_download_enabled", False)):
            return StepResult("SKIPPED", {"reason": "IMAGE_SYNC_DISABLED", "optional": True})
        rid = delegated_run_id()
        if not rid or dry_run:
            return StepResult("SKIPPED", {"reason": "NO_FORMAL_COMMIT", "optional": True})
        from ..images.service import sync_formal_current
        result = sync_formal_current(cfg, export_date=business_date, run_id=rid)
        failed = int(result.get("download_failed_count", 0) or result.get("derivative_failed_count", 0) or 0)
        return StepResult("FAILED" if failed else "SUCCESS", result, retryable=bool(failed), error_code="IMAGE_SYNC_FAILED" if failed else None)

    def knowledge_step() -> StepResult:
        rid = delegated_run_id()
        if not rid or dry_run:
            return StepResult("SKIPPED", {"reason": "NO_FORMAL_COMMIT", "optional": True})
        from ..dictionary_enrichment import enrich_dictionary
        try:
            return StepResult("SUCCESS", enrich_dictionary(cfg, run_id=rid))
        except Exception as exc:
            return StepResult("FAILED", {}, retryable=False, error_code=type(exc).__name__)

    def review_step() -> StepResult:
        rid = delegated_run_id()
        if not rid or dry_run:
            return StepResult("SKIPPED", {"reason": "NO_FORMAL_COMMIT", "optional": True})
        from ..review_queue import build_review_queue
        return StepResult("SUCCESS", build_review_queue(cfg, run_id=rid))

    def qa_step() -> StepResult:
        if dry_run:
            return StepResult("SKIPPED", {"reason": "DRY_RUN_NO_FORMAL_QA_COMMIT"})
        qa = (delegated.get("result") or {}).get("qa") or {}
        return StepResult("SUCCESS" if bool(qa.get("passed")) else "BLOCKED", {"delegated": True, "qa": qa}, error_code=None if qa.get("passed") else "QA_FAILED")

    def db_commit_step() -> StepResult:
        if dry_run:
            return StepResult("SKIPPED", {"reason": "DRY_RUN_NO_FORMAL_COMMIT"})
        status = str((delegated.get("result") or {}).get("commit_status") or "")
        return StepResult("SUCCESS", {"delegated": True, "commit_status": status})

    def export_step() -> StepResult:
        if dry_run:
            return StepResult("SKIPPED", {"reason": "DRY_RUN_NO_FORMAL_EXPORT"})
        status = str((delegated.get("result") or {}).get("commit_status") or "")
        if status == "DB_COMMITTED_EXPORT_PENDING":
            return StepResult("FAILED", {"reason": "COMPATIBILITY_EXPORT_PENDING", "commit_status": status}, retryable=True, error_code="EXPORT_PENDING")
        return StepResult("SUCCESS", {"delegated": True, "commit_status": status, "reason": "compatibility export handled by delegated chain"})

    steps: dict[str, Any] = {
        "PREFLIGHT": lambda: StepResult("SUCCESS", {**production_preflight(cfg, db, report_root), "code_version": git_commit_info()}),
        "BACKUP": lambda: StepResult("SUCCESS", backup_sqlite(db, Path(cfg["paths"]["backups"]) / business_date / f"{run_id}.sqlite3", run_id=run_id, code_version=git_commit_info())),
        "COLLECTION": collect_existing_chain,
        "QA": qa_step,
        "DB_COMMIT": db_commit_step,
        "EXPORT": export_step,
        "IMAGE": image_step,
        "KNOWLEDGE": knowledge_step,
        "AI": lambda: StepResult("SKIPPED", {"reason": "AI_DISABLED"}),
        "AUTO_APPROVAL": lambda: StepResult("SKIPPED", {"reason": "AUTO_APPROVAL_DISABLED"}),
        "REVIEW": review_step,
        "REPORT": lambda: StepResult("SUCCESS"),
    }
    runner = ProductionRunner(root=root, business_date=business_date, run_id=run_id, steps=steps, lock_dir=Path(cfg["paths"]["state"]))
    # Backup artifact needs the generated run id in its manifest; runner's
    # backup step is still safe if the destination is fixed per business date.
    result = runner.run(resume=resume, from_step=from_step)
    if auto_selected:
        selection = {"code": "AUTO_SELECTED_RESUME_RUN", "run_id": run_id}
        result["resume_selection"] = selection
        state_path = report_root / business_date / str(run_id) / "state.json"
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["resume_selection"] = selection
            tmp = state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(state_path)
        except (OSError, json.JSONDecodeError):
            # The run result remains usable; the selection is still explicit
            # in stdout even if evidence persistence is unavailable.
            pass
    return result


_RESUMABLE_STATES = {"DEGRADED", "FAILED", "BLOCKED", "RECOVERY_REQUIRED", "RUNNING"}


def _resolve_resume_run(report_root: Path, business_date: str, requested: str | None) -> tuple[str, bool]:
    day_root = report_root / business_date
    if requested:
        state_path = day_root / requested / "state.json"
        if not state_path.exists():
            raise FileNotFoundError("RUN_STATE_MISSING")
        return requested, False
    candidates: list[tuple[str, str, float]] = []
    if day_root.exists():
        for state_path in day_root.glob("*/state.json"):
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            status = str(state.get("state") or "")
            if status not in _RESUMABLE_STATES:
                continue
            if status == "RUNNING" and _pid_alive(state.get("pid")):
                continue
            candidates.append((str(state.get("started_at") or ""), state_path.parent.name, state_path.stat().st_mtime))
    if not candidates:
        raise FileNotFoundError("RUN_STATE_MISSING")
    candidates.sort(key=lambda item: (item[0], item[2]), reverse=True)
    return candidates[0][1], True


def _pid_alive(pid: Any) -> bool:
    try:
        value = int(pid)
        if value <= 0:
            return False
        os.kill(value, 0)
        return True
    except (TypeError, ValueError, OSError):
        return False
