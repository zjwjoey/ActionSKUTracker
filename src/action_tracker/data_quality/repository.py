from __future__ import annotations

"""Persistence for issues, metrics and repair workflow metadata."""

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Iterable, Mapping, Any

from ..database.connection import connect
from .contracts import DataQualityIssue, CollectionMetric, canonical_json
from .schema import ensure_data_quality_schema


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DataQualityRepository:
    def __init__(self, db_path: Path, *, ensure_schema: bool = True):
        self.path = Path(db_path)
        if ensure_schema:
            ensure_data_quality_schema(self.path)

    def save_issues(self, issues: Iterable[DataQualityIssue]) -> int:
        rows = list(issues)
        with connect(self.path) as db:
            for issue in rows:
                d = issue.as_dict()
                db.execute(
                    """INSERT INTO data_quality_issues
                    (issue_id,issue_type,severity,scope,official_sku,canonical_id,run_id,field_name,
                     current_value,expected_rule,evidence_json,source_hash,source_commit_id,status,
                     resolution_type,resolution_note,detected_at,resolved_at,resolved_by)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(issue_id) DO UPDATE SET
                     current_value=excluded.current_value,evidence_json=excluded.evidence_json,
                     source_hash=excluded.source_hash,source_commit_id=excluded.source_commit_id,
                     expected_rule=excluded.expected_rule,status=CASE
                       WHEN data_quality_issues.status IN ('RESOLVED','WAIVED','SUPERSEDED')
                       THEN data_quality_issues.status ELSE excluded.status END""",
                    (d["issue_id"], d["issue_type"], d["severity"], d["scope"], d["official_sku"],
                     d["canonical_id"], d["run_id"], d["field_name"],
                     None if d["current_value"] is None else str(d["current_value"]), d["expected_rule"],
                     d["evidence_json"], d["source_hash"], d["source_commit_id"], d["status"],
                     d["resolution_type"], d["resolution_note"], d["detected_at"] or now_utc(),
                     d["resolved_at"], d["resolved_by"]),
                )
        return len(rows)

    def list_issues(self, *, status: str | None = None, scope: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM data_quality_issues WHERE 1=1"
        args: list[Any] = []
        if status:
            query += " AND status=?"; args.append(status)
        if scope:
            query += " AND scope=?"; args.append(scope)
        query += " ORDER BY severity,issue_type,official_sku,issue_id"
        with connect(self.path) as db:
            return [dict(row) for row in db.execute(query, args).fetchall()]

    def save_metrics(self, metrics: Iterable[CollectionMetric]) -> int:
        rows = list(metrics)
        with connect(self.path) as db:
            for metric in rows:
                d = metric.as_dict()
                db.execute(
                    """INSERT INTO collection_quality_metrics
                    (metric_id,run_id,metric_name,metric_scope,metric_value,numerator,denominator,
                     baseline_7d,baseline_30d,delta_7d,delta_30d,gate_status,evidence_json,created_at,observation_date)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(run_id,metric_name,metric_scope) DO UPDATE SET
                     metric_value=excluded.metric_value,numerator=excluded.numerator,
                     denominator=excluded.denominator,baseline_7d=excluded.baseline_7d,
                     baseline_30d=excluded.baseline_30d,delta_7d=excluded.delta_7d,
                     delta_30d=excluded.delta_30d,gate_status=excluded.gate_status,
                     evidence_json=excluded.evidence_json,observation_date=excluded.observation_date""",
                    # SQLite treats NULLs as distinct in UNIQUE constraints;
                    # normalize the optional scope to an empty key so reruns
                    # of the same run/metric really are idempotent.
                    (d["metric_id"], d["run_id"], d["metric_name"], d["metric_scope"] or "", d["metric_value"],
                     d["numerator"], d["denominator"], d["baseline_7d"], d["baseline_30d"],
                     d["delta_7d"], d["delta_30d"], d["gate_status"], d["evidence_json"],
                     d["created_at"] or now_utc(), d.get("observation_date")),
                )
        return len(rows)

    def get_metrics(self, run_id: str | None = None) -> list[dict[str, Any]]:
        with connect(self.path) as db:
            if run_id:
                rows = db.execute("SELECT * FROM collection_quality_metrics WHERE run_id=? ORDER BY metric_name,metric_scope", (run_id,)).fetchall()
            else:
                rows = db.execute("SELECT * FROM collection_quality_metrics ORDER BY created_at,metric_name").fetchall()
            return [dict(row) for row in rows]

    def create_batch(self, *, batch_id: str, base_commit_id: str | None, created_by: str,
                     status: str = "DRAFT") -> None:
        with connect(self.path) as db:
            db.execute("""INSERT OR IGNORE INTO repair_batches
                (repair_batch_id,base_commit_id,created_at,created_by,status)
                VALUES(?,?,?,?,?)""", (batch_id, base_commit_id, now_utc(), created_by, status))

    def update_batch(self, batch_id: str, **values: Any) -> None:
        if not values:
            return
        allowed = {"status","issue_count","candidate_count","approved_count","applied_count",
                   "result_commit_id","verification_status"}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"REPAIR_BATCH_FIELD_INVALID:{','.join(sorted(unknown))}")
        clause = ",".join(f"{key}=?" for key in values)
        with connect(self.path) as db:
            db.execute(f"UPDATE repair_batches SET {clause} WHERE repair_batch_id=?", (*values.values(), batch_id))

    def save_candidate(self, candidate: Mapping[str, Any]) -> str:
        with connect(self.path) as db:
            db.execute("""INSERT INTO repair_candidates
                (candidate_id,repair_batch_id,issue_id,official_sku,field_name,old_value,proposed_value,
                 evidence_json,source_hash,confidence,candidate_status,reviewed_by,reviewed_at,repair_action,
                 applied_by,applied_at,verified_by,verified_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(candidate_id) DO UPDATE SET
                 proposed_value=excluded.proposed_value,evidence_json=excluded.evidence_json,
                 confidence=excluded.confidence,repair_action=excluded.repair_action""",
                (candidate["candidate_id"], candidate["repair_batch_id"], candidate["issue_id"],
                 candidate.get("official_sku"), candidate.get("field_name"), candidate.get("old_value"),
                 candidate.get("proposed_value"), candidate.get("evidence_json") or canonical_json(candidate.get("evidence") or {}),
                 candidate.get("source_hash"), candidate.get("confidence"), candidate.get("candidate_status", "PROPOSED"),
                 candidate.get("reviewed_by"), candidate.get("reviewed_at"), candidate.get("repair_action", "NO_AUTOMATIC_REPAIR"),
                 candidate.get("applied_by"), candidate.get("applied_at"), candidate.get("verified_by"), candidate.get("verified_at")),
            )
        return str(candidate["candidate_id"])

    def candidates(self, batch_id: str) -> list[dict[str, Any]]:
        with connect(self.path) as db:
            return [dict(row) for row in db.execute("SELECT * FROM repair_candidates WHERE repair_batch_id=? ORDER BY candidate_id", (batch_id,)).fetchall()]

    def batch(self, batch_id: str) -> dict[str, Any] | None:
        with connect(self.path) as db:
            row = db.execute("SELECT * FROM repair_batches WHERE repair_batch_id=?", (batch_id,)).fetchone()
            return dict(row) if row else None

    def set_candidate_status(self, candidate_id: str, status: str, *, reviewer: str | None = None) -> None:
        with connect(self.path) as db:
            db.execute("UPDATE repair_candidates SET candidate_status=?,reviewed_by=?,reviewed_at=? WHERE candidate_id=?",
                       (status, reviewer, now_utc() if reviewer else None, candidate_id))
