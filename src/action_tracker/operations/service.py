"""Read-only operational view over existing SQLite and filesystem reports."""
from __future__ import annotations

import json
import shutil
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..database.connection import connect
from ..database.production import database_status, validate_production_database


class OperationsService:
    def __init__(self, db_path: Path, *, reports_root: Path | None = None, lock_path: Path | None = None, config: dict[str, Any] | None = None):
        self.db_path = Path(db_path); self.reports_root = Path(reports_root or self.db_path.parent.parent / "reports" / "daily"); self.lock_path = Path(lock_path or self.db_path.parent.parent / "state" / "daily-run.lock"); self.config = config or {}

    def system_status(self) -> dict[str, Any]:
        if not self.db_path.exists():
            return {"state": "UNHEALTHY", "current_sku": 0, "database": {"exists": False, "path": str(self.db_path)}, "reviews_open": 0, "translation_queue_pending": 0, "translation_candidates": 0, "images": {}, "active_lock": self.lock_path.exists()}
        status = database_status(self.db_path)
        with connect(self.db_path) as db:
            current = db.execute("SELECT count(*) FROM products WHERE status='CURRENT'").fetchone()[0]
            reviews = db.execute("SELECT count(*) FROM reviews WHERE status IN ('PENDING','OPEN')").fetchone()[0]
            queue = db.execute("SELECT count(*) FROM translation_queue WHERE status IN ('PENDING','RETRY','RUNNING')").fetchone()[0]
            candidates = db.execute("SELECT count(*) FROM translation_candidates").fetchone()[0]
            images = {str(row[0]): row[1] for row in db.execute("SELECT status,count(*) FROM image_assets GROUP BY status")}
        state = "HEALTHY" if status.get("pending_export_sync", 0) == 0 else "DEGRADED"
        if self.lock_path.exists(): state = "RUNNING"
        return {"state": state, "current_sku": current, "database": status, "reviews_open": reviews, "translation_queue_pending": queue, "translation_candidates": candidates, "images": images, "active_lock": self.lock_path.exists()}

    def latest_run(self) -> dict[str, Any] | None:
        if not self.db_path.exists(): return None
        with connect(self.db_path) as db:
            row = db.execute("SELECT run_id,run_date,status,qa_state,dry_run,started_at,ended_at FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def run_history(self, limit: int = 30) -> list[dict[str, Any]]:
        if not self.db_path.exists(): return []
        with connect(self.db_path) as db:
            rows = db.execute("SELECT run_id,run_date,status,qa_state,dry_run,started_at,ended_at FROM runs ORDER BY started_at DESC LIMIT ?", (max(1, min(int(limit), 90)),)).fetchall()
        return [dict(row) for row in rows]

    def run_detail(self, run_id: str) -> dict[str, Any]:
        result = {"run_id": run_id, "database": None, "artifacts": {}}
        if not self.db_path.exists(): return result
        with connect(self.db_path) as db:
            row = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if row: result["database"] = dict(row)
        for path in self.reports_root.glob(f"*/{run_id}/*.json"):
            if path.name in {"summary.json", "steps.json", "errors.json"}:
                try: result["artifacts"][path.name] = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError): pass
        return result

    def data_quality(self) -> dict[str, Any]:
        if not self.db_path.exists(): return {"current": 0, "duplicate_sku": 0, "invalid_price": 0, "missing_product_url": 0, "presence_unknown": 0, "lifecycle_exceptions": 0, "integrity": "missing", "foreign_key_errors": 0}
        with connect(self.db_path) as db:
            return {"current": db.execute("SELECT count(*) FROM products WHERE status='CURRENT'").fetchone()[0],
                    "duplicate_sku": db.execute("SELECT count(*)-count(DISTINCT official_sku) FROM products").fetchone()[0],
                    "invalid_price": db.execute("SELECT count(*) FROM products WHERE status='CURRENT' AND (current_price IS NULL OR current_price<=0)").fetchone()[0],
                    "missing_product_url": db.execute("SELECT count(*) FROM products WHERE status='CURRENT' AND nullif(trim(product_url),'') IS NULL").fetchone()[0],
                    "presence_unknown": db.execute("SELECT count(*) FROM observations WHERE presence_state='UNKNOWN'").fetchone()[0],
                    "lifecycle_exceptions": db.execute("SELECT count(*) FROM lifecycle_state WHERE current_status NOT IN ('CURRENT','OFFLINE','UNKNOWN')").fetchone()[0],
                    "integrity": db.execute("PRAGMA integrity_check").fetchone()[0], "foreign_key_errors": len(db.execute("PRAGMA foreign_key_check").fetchall())}

    def export_status(self) -> dict[str, Any]:
        if not self.db_path.exists(): return {"statuses": {}, "error": "DB_MISSING"}
        with connect(self.db_path) as db:
            rows = db.execute("SELECT status,count(*) FROM export_sync GROUP BY status").fetchall()
        return {"statuses": {str(row[0]): row[1] for row in rows}}

    def health(self) -> dict[str, Any]:
        checks: dict[str, Any] = {}
        try: checks["database"] = validate_production_database(self.db_path)
        except Exception as exc: checks["database"] = {"status": "FAIL", "error": str(exc)}
        checks["disk_free_bytes"] = shutil.disk_usage(self.db_path.parent).free
        checks["runtime_writable"] = self.db_path.parent.exists() and self.db_path.parent.is_dir()
        checks["latest_run"] = self.latest_run()
        checks["lock"] = {"active": self.lock_path.exists()}
        checks["export"] = self.export_status()
        overall = "HEALTHY" if checks["database"].get("integrity") == "PASS" and checks["database"].get("foreign_keys") == "PASS" else "UNHEALTHY"
        if checks["export"].get("statuses", {}).get("FAILED") or checks["export"].get("statuses", {}).get("PENDING"): overall = "DEGRADED"
        return {"state": overall, "checks": checks}

    def safe_action(self, action: str, *, confirmed: bool = False, run_id: str | None = None) -> dict[str, Any]:
        allowed = {"retry-export-sync": "python -m action_tracker sync-exports", "retry-image-sync": "python -m action_tracker image-sync --date <YYYY-MM-DD>", "resume-run": "python -m action_tracker production-run --resume", "build-translation-queue": "python -m action_tracker dictionary-enrich --run-id <RUN_ID>"}
        if action not in allowed: return {"status": "REJECTED", "reason": "ACTION_NOT_ALLOWED"}
        if not confirmed: return {"status": "CONFIRMATION_REQUIRED", "action": action, "command": allowed[action]}
        # V1 does not execute writes from the read-only console; operators use
        # the existing CLI contract after seeing the confirmation prompt.
        result = {"status": "READY", "action": action, "command": allowed[action], "run_id": run_id}
        action_id = hashlib.sha256(json.dumps([action, run_id, result], sort_keys=True).encode()).hexdigest()
        with connect(self.db_path) as db:
            db.execute("INSERT OR REPLACE INTO operations_actions(action_id,action,target_run_id,parameters_json,result_json,created_at) VALUES(?,?,?,?,?,?)",
                       (action_id, action, run_id, json.dumps({"confirmed": True}, ensure_ascii=False), json.dumps(result, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))
        result["action_id"] = action_id
        return result
