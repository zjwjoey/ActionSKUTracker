from __future__ import annotations
from pathlib import Path
from .connection import connect

DDL = '''
CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS products (
 canonical_id TEXT PRIMARY KEY, official_sku TEXT UNIQUE NOT NULL, name_es TEXT, name_zh TEXT,
 current_price REAL, original_price REAL, unit_price_raw TEXT, raw_badges TEXT,
 action_new_badge INTEGER NOT NULL DEFAULT 0, promotion_active INTEGER NOT NULL DEFAULT 0,
 sustainable_badge INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, consecutive_missing INTEGER NOT NULL DEFAULT 0,
 product_url TEXT, image_url TEXT, first_seen_at TEXT, last_seen_at TEXT, last_checked_at TEXT,
 price_hash TEXT, content_hash TEXT, source_hash TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS product_observations (id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, observation_date TEXT NOT NULL, canonical_id TEXT NOT NULL, official_sku TEXT NOT NULL, sitemap_seen INTEGER, listing_seen INTEGER, current_price REAL, original_price REAL, raw_json TEXT NOT NULL, UNIQUE(run_id, official_sku));
CREATE TABLE IF NOT EXISTS price_history (id INTEGER PRIMARY KEY, canonical_id TEXT NOT NULL, official_sku TEXT NOT NULL, observed_at TEXT NOT NULL, old_price REAL, new_price REAL NOT NULL, change_type TEXT NOT NULL, run_id TEXT);
CREATE TABLE IF NOT EXISTS event_history (id INTEGER PRIMARY KEY, canonical_id TEXT NOT NULL, official_sku TEXT NOT NULL, occurred_at TEXT NOT NULL, event_type TEXT NOT NULL, old_value TEXT, new_value TEXT, run_id TEXT, evidence TEXT);
CREATE TABLE IF NOT EXISTS translations (canonical_id TEXT PRIMARY KEY, official_sku TEXT NOT NULL, source_hash TEXT, translation_status TEXT NOT NULL, translated_at TEXT, name_zh TEXT, description_zh TEXT, product_details_zh TEXT);
CREATE TABLE IF NOT EXISTS image_map (canonical_id TEXT PRIMARY KEY, official_sku TEXT NOT NULL, local_image_path TEXT, source_image_url TEXT, image_hash TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, run_date TEXT NOT NULL, status TEXT NOT NULL, qa_state TEXT, dry_run INTEGER NOT NULL, started_at TEXT, ended_at TEXT, schema_version TEXT);
CREATE TABLE IF NOT EXISTS sync_queue (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, action TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING');
'''
def migrate(path):
    with connect(path) as db:
        db.executescript(DDL)
        db.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES ('1.0.0')")


V2_DDL = '''
CREATE TABLE IF NOT EXISTS schema_metadata (
 key TEXT PRIMARY KEY,
 value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS product_localizations (
 official_sku TEXT NOT NULL,
 language TEXT NOT NULL,
 name TEXT, cat1 TEXT, cat2 TEXT, spec TEXT, description TEXT, details TEXT,
 source TEXT, review_status TEXT, updated_at TEXT NOT NULL, last_commit_id TEXT,
 PRIMARY KEY (official_sku, language),
 FOREIGN KEY (official_sku) REFERENCES products(official_sku)
);
CREATE TABLE IF NOT EXISTS lifecycle_state (
 official_sku TEXT PRIMARY KEY,
 canonical_id TEXT NOT NULL,
 first_seen_date TEXT,
 last_seen_date TEXT,
 current_status TEXT NOT NULL,
 missing_count INTEGER NOT NULL DEFAULT 0,
 last_missing_date TEXT,
 offline_date TEXT,
 last_state_observation_date TEXT,
 ever_offline INTEGER NOT NULL DEFAULT 0,
 last_run_id TEXT,
 updated_at TEXT NOT NULL,
 FOREIGN KEY (official_sku) REFERENCES products(official_sku)
);
CREATE TABLE IF NOT EXISTS observations (
 run_id TEXT NOT NULL,
 official_sku TEXT NOT NULL,
 observation_date TEXT NOT NULL,
 presence_state TEXT NOT NULL CHECK (presence_state IN ('PRESENT','ABSENT','UNKNOWN')),
 sitemap_present INTEGER,
 listing_present INTEGER,
 nuevo_present INTEGER,
 promotion_present INTEGER,
 observation_complete INTEGER NOT NULL DEFAULT 0,
 absence_capable INTEGER NOT NULL DEFAULT 0,
 current_price REAL,
 source_flag TEXT,
 PRIMARY KEY (run_id, official_sku),
 FOREIGN KEY (official_sku) REFERENCES products(official_sku)
);
CREATE TABLE IF NOT EXISTS commit_batches (
 commit_id TEXT PRIMARY KEY,
 run_id TEXT NOT NULL UNIQUE,
 base_commit_id TEXT,
 bundle_hash TEXT NOT NULL,
 schema_version TEXT NOT NULL,
 started_at TEXT NOT NULL,
 committed_at TEXT NOT NULL,
 product_count INTEGER NOT NULL DEFAULT 0,
 observation_count INTEGER NOT NULL DEFAULT 0,
 price_event_count INTEGER NOT NULL DEFAULT 0,
 event_count INTEGER NOT NULL DEFAULT 0,
 snapshot_path TEXT,
 snapshot_hash TEXT,
 status TEXT NOT NULL CHECK (status IN ('COMMITTED','ROLLED_BACK','FAILED'))
);
CREATE TABLE IF NOT EXISTS run_evidence (
 run_id TEXT PRIMARY KEY,
 snapshot_path TEXT,
 snapshot_hash TEXT,
 evidence_json TEXT NOT NULL,
 FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
CREATE TABLE IF NOT EXISTS reviews (
 review_id TEXT PRIMARY KEY,
 run_id TEXT NOT NULL,
 entity_id TEXT NOT NULL,
 issue_type TEXT NOT NULL,
 evidence TEXT,
 suggested_action TEXT,
 status TEXT NOT NULL DEFAULT 'PENDING',
 decision TEXT,
 decision_value TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
CREATE TABLE IF NOT EXISTS source_records (
 source_record_id TEXT PRIMARY KEY,
 run_id TEXT NOT NULL,
 source_name TEXT NOT NULL,
 official_sku TEXT,
 observed_at TEXT,
 payload_json TEXT NOT NULL,
 source_hash TEXT,
 FOREIGN KEY (run_id) REFERENCES runs(run_id),
 FOREIGN KEY (official_sku) REFERENCES products(official_sku)
);
CREATE TABLE IF NOT EXISTS migration_source_issues (
 issue_id INTEGER PRIMARY KEY,
 source_name TEXT NOT NULL,
 issue_type TEXT NOT NULL,
 entity_id TEXT,
 details TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS export_sync (
 commit_id TEXT PRIMARY KEY,
 master_status TEXT NOT NULL DEFAULT 'PENDING',
 known_status TEXT NOT NULL DEFAULT 'PENDING',
 offline_status TEXT NOT NULL DEFAULT 'PENDING',
 master_sha256 TEXT,
 known_sha256 TEXT,
 offline_sha256 TEXT,
 last_attempt_at TEXT,
 error TEXT,
 status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','SUCCESS','FAILED')),
 FOREIGN KEY (commit_id) REFERENCES commit_batches(commit_id)
);
CREATE TABLE IF NOT EXISTS image_assets (
 official_sku TEXT PRIMARY KEY,
 canonical_id TEXT,
 source_image_url TEXT,
 master_image_path TEXT,
 source_hash TEXT,
 master_hash TEXT,
 width INTEGER,
 height INTEGER,
 status TEXT NOT NULL,
 first_downloaded_at TEXT,
 last_checked_at TEXT,
 updated_at TEXT NOT NULL,
 error_type TEXT,
 FOREIGN KEY (official_sku) REFERENCES products(official_sku)
);
CREATE TABLE IF NOT EXISTS operations_actions (
 action_id TEXT PRIMARY KEY,
 action TEXT NOT NULL,
 target_run_id TEXT,
 parameters_json TEXT NOT NULL,
 result_json TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE VIEW IF NOT EXISTS events AS
 SELECT id,canonical_id,official_sku,occurred_at,event_type,old_value,new_value,run_id,evidence,event_key
 FROM event_history;

CREATE TABLE IF NOT EXISTS translation_resolution (
 resolution_id TEXT PRIMARY KEY,
 official_sku TEXT NOT NULL,
 language TEXT NOT NULL DEFAULT 'zh',
 source_hash TEXT NOT NULL,
 base_commit_id TEXT,
 dictionary_hash TEXT,
 readiness TEXT NOT NULL,
 fields_json TEXT NOT NULL,
 reasons_json TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(official_sku, language, source_hash),
 FOREIGN KEY (official_sku) REFERENCES products(official_sku)
);
CREATE TABLE IF NOT EXISTS translation_queue (
 queue_id TEXT PRIMARY KEY,
 official_sku TEXT NOT NULL,
 language TEXT NOT NULL DEFAULT 'zh',
 source_hash TEXT NOT NULL,
 requested_fields TEXT NOT NULL,
 reason TEXT NOT NULL,
 priority TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'PENDING',
 retry_count INTEGER NOT NULL DEFAULT 0,
 last_error TEXT,
 run_id TEXT,
 created_at TEXT NOT NULL,
 claimed_at TEXT,
 completed_at TEXT,
 UNIQUE(official_sku, language, source_hash, requested_fields),
 FOREIGN KEY (official_sku) REFERENCES products(official_sku)
);
CREATE TABLE IF NOT EXISTS translation_candidates (
 candidate_id TEXT PRIMARY KEY,
 queue_id TEXT NOT NULL,
 official_sku TEXT NOT NULL,
 language TEXT NOT NULL DEFAULT 'zh',
 source_hash TEXT NOT NULL,
 model_provider TEXT,
 model_name TEXT,
 prompt_version TEXT NOT NULL,
 fields_json TEXT NOT NULL,
 confidence REAL,
 validation_status TEXT NOT NULL,
 approval_status TEXT NOT NULL DEFAULT 'PENDING',
 created_at TEXT NOT NULL,
 UNIQUE(queue_id, prompt_version, model_name),
 FOREIGN KEY (queue_id) REFERENCES translation_queue(queue_id),
 FOREIGN KEY (official_sku) REFERENCES products(official_sku)
);
CREATE TABLE IF NOT EXISTS translation_approval_audit (
 decision_id TEXT PRIMARY KEY,
 candidate_id TEXT NOT NULL,
 official_sku TEXT NOT NULL,
 field_name TEXT NOT NULL,
 decision TEXT NOT NULL,
 policy_version TEXT NOT NULL,
 rules_passed TEXT NOT NULL,
 rules_failed TEXT NOT NULL,
 source_hash TEXT NOT NULL,
 decided_at TEXT NOT NULL,
 UNIQUE(candidate_id, field_name, policy_version),
 FOREIGN KEY (candidate_id) REFERENCES translation_candidates(candidate_id),
 FOREIGN KEY (official_sku) REFERENCES products(official_sku)
);
'''


def migrate_v2(path, *, role: str = "SHADOW"):
    """Additive V2 production tables while preserving the frozen V1 mirror schema."""
    if role not in {"SHADOW", "PRIMARY"}:
        raise ValueError("DB_ROLE_INVALID")
    # A migration is allowed to create a role on a new database, but it must
    # never silently change the role of an existing V2 database.  Promotion is
    # an explicit operation handled by ``promote_database_role``.
    existing_role = None
    if Path(path).exists():
        try:
            with connect(path) as existing:
                existing_role = existing.execute(
                    "SELECT value FROM schema_metadata WHERE key='database_role'"
                ).fetchone()
        except Exception:
            existing_role = None
    if existing_role and str(existing_role[0]) != role:
        raise ValueError("DB_ROLE_MISMATCH_REQUIRES_EXPLICIT_CUTOVER")
    migrate(path)
    with connect(path) as db:
        db.executescript(V2_DDL)
        for key, value in {
            "schema_family": "ACTION_SQLITE_DATA",
            "schema_version": "2.0.0",
            "database_role": role,
        }.items():
            db.execute("INSERT INTO schema_metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        for table, column, definition in (
            ("price_history", "event_key", "TEXT"),
            ("event_history", "event_key", "TEXT"),
        ):
            try:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            except Exception as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
        # Additive localization provenance/freshness columns.  Existing V2
        # databases remain readable and are upgraded in-place without
        # rewriting any product facts.
        for column, definition in (
            ("source_hash", "TEXT"),
            ("resolution_status", "TEXT"),
            ("name_source", "TEXT"),
            ("cat1_source", "TEXT"),
            ("cat2_source", "TEXT"),
            ("spec_source", "TEXT"),
            ("description_source", "TEXT"),
            ("details_source", "TEXT"),
            ("freshness_status", "TEXT"),
            ("approved_by", "TEXT"),
            ("approved_at", "TEXT"),
            ("applied_commit_id", "TEXT"),
        ):
            try:
                db.execute(f"ALTER TABLE product_localizations ADD COLUMN {column} {definition}")
            except Exception as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_price_history_event_key ON price_history(event_key) WHERE event_key IS NOT NULL")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_event_history_event_key ON event_history(event_key) WHERE event_key IS NOT NULL")
