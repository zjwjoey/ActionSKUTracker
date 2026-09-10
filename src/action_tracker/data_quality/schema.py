from __future__ import annotations

"""Additive schema for Data Quality & Integrity V1."""

from pathlib import Path
from ..database.connection import connect
from ..database.schema import migrate_v2


DATA_QUALITY_DDL = """
CREATE TABLE IF NOT EXISTS data_quality_issues (
 issue_id TEXT PRIMARY KEY,
 issue_type TEXT NOT NULL,
 severity TEXT NOT NULL,
 scope TEXT NOT NULL,
 official_sku TEXT,
 canonical_id TEXT,
 run_id TEXT,
 field_name TEXT,
 current_value TEXT,
 expected_rule TEXT NOT NULL,
 evidence_json TEXT NOT NULL,
 source_hash TEXT,
 source_commit_id TEXT,
 status TEXT NOT NULL DEFAULT 'OPEN',
 resolution_type TEXT,
 resolution_note TEXT,
 detected_at TEXT NOT NULL,
 resolved_at TEXT,
 resolved_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_dq_issue_scope_status ON data_quality_issues(scope,status,severity);
CREATE INDEX IF NOT EXISTS idx_dq_issue_sku ON data_quality_issues(official_sku,issue_type);
CREATE INDEX IF NOT EXISTS idx_dq_issue_run ON data_quality_issues(run_id,issue_type);

CREATE TABLE IF NOT EXISTS collection_quality_metrics (
 metric_id TEXT PRIMARY KEY,
 run_id TEXT NOT NULL,
 metric_name TEXT NOT NULL,
 metric_scope TEXT,
 metric_value REAL,
 numerator REAL,
 denominator REAL,
 baseline_7d REAL,
 baseline_30d REAL,
 delta_7d REAL,
 delta_30d REAL,
 gate_status TEXT NOT NULL,
 evidence_json TEXT,
 created_at TEXT NOT NULL,
 UNIQUE(run_id,metric_name,metric_scope)
);
CREATE INDEX IF NOT EXISTS idx_collection_metrics_name_date ON collection_quality_metrics(metric_name,created_at);

CREATE TABLE IF NOT EXISTS repair_batches (
 repair_batch_id TEXT PRIMARY KEY,
 base_commit_id TEXT,
 created_at TEXT NOT NULL,
 created_by TEXT NOT NULL,
 status TEXT NOT NULL,
 issue_count INTEGER NOT NULL DEFAULT 0,
 candidate_count INTEGER NOT NULL DEFAULT 0,
 approved_count INTEGER NOT NULL DEFAULT 0,
 applied_count INTEGER NOT NULL DEFAULT 0,
 result_commit_id TEXT,
 verification_status TEXT
);
CREATE TABLE IF NOT EXISTS repair_candidates (
 candidate_id TEXT PRIMARY KEY,
 repair_batch_id TEXT NOT NULL,
 issue_id TEXT NOT NULL,
 official_sku TEXT,
 field_name TEXT,
 old_value TEXT,
 proposed_value TEXT,
 evidence_json TEXT NOT NULL,
 source_hash TEXT,
 confidence REAL,
 candidate_status TEXT NOT NULL DEFAULT 'PROPOSED',
 reviewed_by TEXT,
 reviewed_at TEXT,
 FOREIGN KEY(repair_batch_id) REFERENCES repair_batches(repair_batch_id),
 FOREIGN KEY(issue_id) REFERENCES data_quality_issues(issue_id),
 UNIQUE(repair_batch_id,issue_id)
);
CREATE INDEX IF NOT EXISTS idx_repair_candidate_status ON repair_candidates(candidate_status,repair_batch_id);
"""


def ensure_data_quality_schema(path: Path, *, role: str = "SHADOW") -> None:
    """Create only additive V1 tables.  Existing facts are never rewritten."""
    path = Path(path)
    if path.exists():
        try:
            with connect(path) as db:
                existing = db.execute("SELECT value FROM schema_metadata WHERE key='database_role'").fetchone()
            if existing:
                role = str(existing[0])
        except Exception:
            pass
    migrate_v2(path, role=role)
    with connect(path) as db:
        db.executescript(DATA_QUALITY_DDL)
