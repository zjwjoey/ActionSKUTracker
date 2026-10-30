from __future__ import annotations

from pathlib import Path
import json
import uuid
from datetime import datetime
from typing import Any

from ..database.integration import database_path
from ..database.production import validate_production_database
from ..services.gitutil import git_commit_info
from ..services.runtime import is_process_alive
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
    from .storage_lifecycle import prepare_run_workspace, record_run_workspace
    cfg, transient_task = prepare_run_workspace(cfg, run_id=run_id,
                                               report_dir=report_root / business_date / run_id)
    delegated: dict[str, Any] = {}
    def is_field_commit() -> bool:
        result = delegated.get('result') or {}
        if result.get('commit_status') not in {'FIELDS_COMMITTED', 'FIELDS_COMMITTED_EXPORT_PENDING'}:
            return False
        import sqlite3
        from ..qa.validator import validate_trusted_field_scope
        with sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True) as connection:
            row = connection.execute('''SELECT e.evidence_json FROM commit_batches c
                JOIN runs r ON r.run_id=c.run_id JOIN run_evidence e ON e.run_id=c.run_id
                WHERE c.commit_id=? AND c.run_id=? AND c.status='COMMITTED' AND r.run_date=? AND r.dry_run=0''',
                (result.get('commit_id'), result.get('run_id'), business_date)).fetchone()
        evidence = json.loads(row[0]) if row else {}
        return (evidence.get('commit_scope') == 'POSITIVE_FACTS_ONLY'
                and validate_trusted_field_scope(evidence.get('trusted_field_scope'),
                    run_id=str(result.get('run_id') or ''), business_date=business_date))
    if resume:
        prior_state = report_root / business_date / str(run_id) / "state.json"
        if prior_state.exists():
            try:
                prior = json.loads(prior_state.read_text(encoding="utf-8"))
                details = prior.get("steps", {}).get("COLLECTION", {}).get("details", {})
                if details.get("delegated_run_id"):
                    # Restore only metadata required by downstream Operations
                    # steps.  This is deliberately an allowlist rather than
                    # replaying arbitrary persisted JSON into runtime state.
                    delegated["result"] = {
                        "run_id": details["delegated_run_id"],
                        "commit_status": details.get("commit_status", ""),
                        "commit_id": details.get("commit_id"),
                        "qa": details.get("qa") or {},
                        "business_date": details.get("business_date"),
                        "registry": details.get("registry") or {},
                    }
            except (OSError, json.JSONDecodeError):
                pass
    def collect_existing_chain() -> StepResult:
        if no_network:
            return StepResult("BLOCKED", {"reason": "NETWORK_DISABLED"})
        # Reuse the established collector/QA/SQLite writer as one atomic
        # business chain. Operations wraps it; it never creates a second
        # crawler or product writer.
        from ..orchestrator.daily import run_daily
        result = _recover_committed_collection(cfg, run_id, business_date) if resume and not dry_run else None
        if result is None:
            try:
                result = run_daily(cfg, dry_run=dry_run, fetch_details=True, _skip_lock=True,
                                   business_date=business_date, workflow_run_id=run_id)
            except Exception as exc:
                result = _recover_committed_collection(cfg, run_id, business_date) if not dry_run else None
                if result is None:
                    raise
                result['post_commit_warning'] = f'{type(exc).__name__}: {exc}'
        delegated["result"] = result
        actual_date = result.get("business_date") or result.get("run_date")
        if actual_date and str(actual_date) != business_date:
            return StepResult("BLOCKED", {"expected": business_date, "actual": actual_date}, error_code="COLLECTION_BUSINESS_DATE_MISMATCH")
        qa = result.get("qa") or {}
        commit_status = str(result.get("commit_status") or "")
        field_commit = is_field_commit()
        if not bool(qa.get("passed")) and not dry_run and not field_commit:
            return StepResult("BLOCKED", {"delegated_run_id": result.get("run_id"), "commit_status": commit_status, "qa": qa})
        # A passed QA report is not sufficient evidence that the formal write
        # completed.  The delegated daily chain can fail while building the
        # SQLite bundle, projecting compatibility files, or replacing state.
        # Only these two statuses mean the chain reached its commit boundary;
        # export-pending is deliberately allowed through so EXPORT can make
        # the outer run DEGRADED and retain a retryable error.
        if not dry_run and commit_status not in {"FULL_COMMIT", "DB_COMMITTED_EXPORT_PENDING"} and not field_commit:
            return StepResult("BLOCKED", {
                "delegated_run_id": result.get("run_id"),
                "commit_status": commit_status,
                "qa": qa,
                "reason": "FORMAL_COMMIT_NOT_CONFIRMED",
            }, error_code="FORMAL_COMMIT_NOT_CONFIRMED")
        registry = result.get("registry") or ((result.get("run_report") or {}).get("sqlite") or {}).get("translation_registry") or {}
        result["registry"] = registry
        return StepResult("SUCCESS", {"delegated": True, "delegated_run_id": result.get("run_id"), "business_date": actual_date, "registry": registry, "commit_status": commit_status, "commit_id": result.get("commit_id"), "qa": qa,
            'commit_scope': 'POSITIVE_FACTS_ONLY' if field_commit else 'FULL_OBSERVATION',
            'post_commit_recovered': bool(result.get('post_commit_recovered')),
            'post_commit_warning': result.get('post_commit_warning')})

    def registry_step() -> StepResult:
        if dry_run or not (cfg.get("localization") or {}).get("registry_enabled", True):
            return StepResult("SKIPPED", {"reason": "REGISTRY_DISABLED_OR_DRY_RUN"})
        result = delegated.get("result") or {}
        registry = dict(result.get("registry") or {})
        status = str(registry.get("status") or "PENDING").upper()
        if status != "SUCCESS" and resume:
            from .registry import _retry_registry_unlocked
            try:
                registry = _retry_registry_unlocked(cfg, source_run_id=str(result.get("run_id") or ""),
                    fact_commit_id=str(result.get("commit_id") or ""), business_date=business_date)
                status = registry["status"]
            except Exception as exc:
                registry = {**registry, "error": str(exc), "status": "FAILED"}
                status = "FAILED"
        details = {**registry, "source_run_id": result.get("run_id"), "fact_commit_id": result.get("commit_id"),
                   "business_date": business_date, "registry_status": status}
        return StepResult("SUCCESS" if status == "SUCCESS" else "FAILED", details,
                          retryable=status != "SUCCESS", error_code=None if status == "SUCCESS" else "REGISTRY_INGEST_PENDING")

    def delegated_run_id() -> str | None:
        result = delegated.get("result") or {}
        return str(result.get("run_id") or "") or None

    def image_step() -> StepResult:
        if is_field_commit():
            return StepResult('SKIPPED', {'reason': 'PRESENCE_NOT_RELEASE_READY'})
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
        if is_field_commit():
            return StepResult('SKIPPED', {'reason': 'PRESENCE_NOT_RELEASE_READY'})
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
        if is_field_commit():
            return StepResult('SUCCESS', {'qa': qa, 'commit_scope': 'POSITIVE_FACTS_ONLY',
                                         'presence_authority': False})
        return StepResult("SUCCESS" if bool(qa.get("passed")) else "BLOCKED", {"delegated": True, "qa": qa}, error_code=None if qa.get("passed") else "QA_FAILED")

    def db_commit_step() -> StepResult:
        if dry_run:
            return StepResult("SKIPPED", {"reason": "DRY_RUN_NO_FORMAL_COMMIT"})
        status = str((delegated.get("result") or {}).get("commit_status") or "")
        return StepResult("SUCCESS", {"delegated": True, "commit_status": status, "commit_id": (delegated.get("result") or {}).get("commit_id")})

    def export_step() -> StepResult:
        if dry_run:
            return StepResult("SKIPPED", {"reason": "DRY_RUN_NO_FORMAL_EXPORT"})
        result = delegated.get("result") or {}
        status = str(result.get("commit_status") or "")
        if is_field_commit():
            if status == 'FIELDS_COMMITTED_EXPORT_PENDING':
                from ..database.integration import regenerate_pending_exports
                recovered = regenerate_pending_exports(cfg, commit_id=result.get('commit_id'))
                if not recovered or any(item.get('status') != 'SUCCESS' for item in recovered):
                    return StepResult('FAILED', {'reason': 'FIELD_COMPATIBILITY_EXPORT_PENDING'},
                                      retryable=True, error_code='EXPORT_PENDING')
                result['commit_status'] = 'FIELDS_COMMITTED'
            return StepResult('SUCCESS', {'commit_scope': 'POSITIVE_FACTS_ONLY',
                'formal_release': False, 'reason': 'Compatibility projection only; Presence remains incomplete'})
        if status == "DB_COMMITTED_EXPORT_PENDING":
            commit_id = str(result.get("commit_id") or "")
            if not commit_id:
                # Retain the established retryable outer state for historical
                # reports that predate commit_id persistence.  New formal runs
                # always include it, so a resume can perform real recovery.
                return StepResult("FAILED", {"reason": "COMMIT_ID_MISSING", "commit_status": status}, retryable=True, error_code="EXPORT_PENDING")
            from ..database.integration import regenerate_pending_exports
            recovered = regenerate_pending_exports(cfg, commit_id=commit_id)
            if not recovered or any(item.get("status") != "SUCCESS" for item in recovered):
                return StepResult("FAILED", {"reason": "COMPATIBILITY_EXPORT_PENDING", "commit_status": status, "commit_id": commit_id, "recovery": recovered}, retryable=True, error_code="EXPORT_PENDING")
            result["commit_status"] = "FULL_COMMIT"
            return StepResult("SUCCESS", {"delegated": True, "commit_status": "FULL_COMMIT", "commit_id": commit_id, "recovery": recovered})
        return StepResult("SUCCESS", {"delegated": True, "commit_status": status, "commit_id": result.get("commit_id"), "reason": "compatibility export handled by delegated chain"})

    steps: dict[str, Any] = {
        "PREFLIGHT": lambda: StepResult("SUCCESS", {**production_preflight(cfg, db, report_root), "code_version": git_commit_info()}),
        "BACKUP": lambda: StepResult("SUCCESS", backup_sqlite(db, Path(cfg["paths"]["backups"]) / business_date / f"{run_id}.sqlite3", run_id=run_id, code_version=git_commit_info())),
        "COLLECTION": collect_existing_chain,
        "QA": qa_step,
        "DB_COMMIT": db_commit_step,
        "REGISTRY": registry_step,
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
    if transient_task is not None:
        try:
            result['transient_workspace'] = record_run_workspace(transient_task)
        except Exception as exc:
            # Never reinterpret an already committed business result as rolled back.
            result['transient_workspace'] = {'task_dir': str(transient_task.directory),
                                             'inventory_status': 'FAILED', 'error_code': type(exc).__name__}
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


def _recover_committed_collection(cfg: dict[str, Any], run_id: str, business_date: str) -> dict[str, Any] | None:
    """Recover an exact delegated identity after a post-commit process failure."""
    import sqlite3
    from ..database.production import CommitBundle, ProductionWriter
    from ..qa.validator import validate_trusted_field_scope

    path = database_path(cfg)
    if not path.exists():
        return None
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute('''SELECT c.commit_id,r.run_date,r.qa_state,r.dry_run,
            e.evidence_json,s.status AS export_status FROM commit_batches c
            JOIN runs r ON r.run_id=c.run_id JOIN run_evidence e ON e.run_id=c.run_id
            LEFT JOIN export_sync s ON s.commit_id=c.commit_id
            WHERE c.run_id=? AND c.status='COMMITTED' ''', (run_id,)).fetchone()
        if row is None:
            return None
        evidence = json.loads(row['evidence_json'] or '{}')
        if (row['run_date'] != business_date or row['dry_run']
                or evidence.get('parent_workflow_run_id') != run_id):
            raise ValueError('COLLECTION_RECOVERY_IDENTITY_MISMATCH')
        partial = evidence.get('commit_scope') == 'POSITIVE_FACTS_ONLY'
        if partial:
            if not validate_trusted_field_scope(evidence.get('trusted_field_scope'),
                                              run_id=run_id, business_date=business_date):
                raise ValueError('COLLECTION_RECOVERY_FIELD_SCOPE_INVALID')
        elif row['qa_state'] not in {'PASS', 'PASS_PRESENCE_ONLY'}:
            raise ValueError('COLLECTION_RECOVERY_QA_NOT_VERIFIED')
        ProductionWriter._validate_collection_quality_evidence(connection, CommitBundle(
            run_id=run_id, observation_date=business_date, qa_state=row['qa_state'],
            run_record=evidence, collection_quality_state=evidence.get('collection_quality_state'),
            requires_collection_integrity=True))
    from .registry import _status_path, record_registry_status
    registry_path = _status_path(cfg, run_id)
    if registry_path.exists():
        registry = json.loads(registry_path.read_text(encoding='utf-8'))
        if registry.get('fact_commit_id') != row['commit_id'] or registry.get('business_date') != business_date:
            raise ValueError('COLLECTION_RECOVERY_REGISTRY_IDENTITY_MISMATCH')
    else:
        registry = record_registry_status(cfg, source_run_id=run_id, fact_commit_id=row['commit_id'],
            business_date=business_date, result={'status': 'PENDING', 'error': 'POST_COMMIT_RECOVERY'})
    status = 'FIELDS_COMMITTED' if partial else 'FULL_COMMIT'
    if row['export_status'] != 'SUCCESS':
        status = 'FIELDS_COMMITTED_EXPORT_PENDING' if partial else 'DB_COMMITTED_EXPORT_PENDING'
    return {'run_id': run_id, 'business_date': business_date, 'commit_id': row['commit_id'],
            'commit_status': status, 'registry': registry, 'post_commit_recovered': True,
            'qa': {'state': row['qa_state'], 'passed': row['qa_state'] in {'PASS', 'PASS_PRESENCE_ONLY'},
                   'issues': evidence.get('qa_issues') or [], 'recovered_from_commit': True}}


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
            if status == "RUNNING" and is_process_alive(state.get("pid")):
                continue
            candidates.append((str(state.get("started_at") or ""), state_path.parent.name, state_path.stat().st_mtime))
    if not candidates:
        raise FileNotFoundError("RUN_STATE_MISSING")
    candidates.sort(key=lambda item: (item[0], item[2]), reverse=True)
    return candidates[0][1], True
