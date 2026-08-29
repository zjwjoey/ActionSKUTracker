from __future__ import annotations

from pathlib import Path

from .connection import connect

SCHEMA_VERSION = "1.0.0"
SCHEMA_FAMILY = "ACTION_SQLITE_MIRROR"

# The legacy tables remain intentionally for compatibility with the existing
# baseline tests and old callers.  V1 Mirror code uses the tables below marked
# "V1" and never writes the legacy products table.
DDL = '''
CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);

-- Legacy compatibility tables (frozen; not used by db-mirror V1).
CREATE TABLE IF NOT EXISTS products_legacy (
 canonical_id TEXT PRIMARY KEY, official_sku TEXT UNIQUE NOT NULL, name_es TEXT, name_zh TEXT,
 current_price REAL, original_price REAL, unit_price_raw TEXT, raw_badges TEXT,
 action_new_badge INTEGER NOT NULL DEFAULT 0, promotion_active INTEGER NOT NULL DEFAULT 0,
 sustainable_badge INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, consecutive_missing INTEGER NOT NULL DEFAULT 0,
 product_url TEXT, image_url TEXT, first_seen_at TEXT, last_seen_at TEXT, last_checked_at TEXT,
 price_hash TEXT, content_hash TEXT, source_hash TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS product_observations (id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, observation_date TEXT NOT NULL, canonical_id TEXT NOT NULL, official_sku TEXT NOT NULL, sitemap_seen INTEGER, listing_seen INTEGER, current_price REAL, original_price REAL, raw_json TEXT NOT NULL, UNIQUE(run_id, official_sku));
CREATE TABLE IF NOT EXISTS price_history_legacy (id INTEGER PRIMARY KEY, canonical_id TEXT NOT NULL, official_sku TEXT NOT NULL, observed_at TEXT NOT NULL, old_price REAL, new_price REAL, change_type TEXT NOT NULL, run_id TEXT);
CREATE TABLE IF NOT EXISTS event_history (id INTEGER PRIMARY KEY, canonical_id TEXT NOT NULL, official_sku TEXT NOT NULL, occurred_at TEXT NOT NULL, event_type TEXT NOT NULL, old_value TEXT, new_value TEXT, run_id TEXT, evidence TEXT);
CREATE TABLE IF NOT EXISTS translations (canonical_id TEXT PRIMARY KEY, official_sku TEXT NOT NULL, source_hash TEXT, translation_status TEXT NOT NULL, translated_at TEXT, name_zh TEXT, description_zh TEXT, product_details_zh TEXT);
CREATE TABLE IF NOT EXISTS image_map (canonical_id TEXT PRIMARY KEY, official_sku TEXT NOT NULL, local_image_path TEXT, source_image_url TEXT, image_hash TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS runs_legacy (run_id TEXT PRIMARY KEY, run_date TEXT NOT NULL, status TEXT NOT NULL, qa_state TEXT, dry_run INTEGER NOT NULL, started_at TEXT, ended_at TEXT, schema_version TEXT);
CREATE TABLE IF NOT EXISTS sync_queue (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, action TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING');

-- SQLite Data Foundation V1.
CREATE TABLE IF NOT EXISTS products (
  sku TEXT PRIMARY KEY,
  canonical_id TEXT NOT NULL UNIQUE,
  name_es TEXT,
  description_es TEXT,
  details_es TEXT,
  cat1_es TEXT,
  cat2_es TEXT,
  spec_es TEXT,
  product_url TEXT,
  image_url TEXT,
  current_price REAL,
  historical_min_price REAL,
  historical_max_price REAL,
  current_status_raw TEXT,
  first_seen_at TEXT,
  last_seen_at TEXT,
  source_sheet TEXT NOT NULL,
  source_row_no INTEGER NOT NULL,
  source_raw_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(source_sheet, source_row_no)
);

CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  run_date TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  git_commit TEXT,
  sitemap_count INTEGER,
  listing_count INTEGER,
  current_count INTEGER,
  new_count INTEGER,
  reappeared_count INTEGER,
  missing_first_count INTEGER,
  missing_continued_count INTEGER,
  offline_count INTEGER,
  price_up_count INTEGER,
  price_down_count INTEGER,
  promo_start_count INTEGER,
  promo_end_count INTEGER,
  access_state TEXT,
  observation_complete INTEGER CHECK (observation_complete IN (0, 1) OR observation_complete IS NULL),
  qa_status TEXT,
  commit_status TEXT,
  snapshot_path TEXT,
  source_row_no INTEGER NOT NULL UNIQUE,
  source_raw_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS product_localizations (
  sku TEXT NOT NULL REFERENCES products(sku) ON DELETE RESTRICT,
  language TEXT NOT NULL,
  name TEXT,
  cat1 TEXT,
  cat2 TEXT,
  spec TEXT,
  description TEXT,
  details TEXT,
  source TEXT NOT NULL,
  source_hash TEXT,
  review_status TEXT NOT NULL,
  source_sheet TEXT NOT NULL,
  source_row_no INTEGER NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (sku, language)
);

CREATE TABLE IF NOT EXISTS observations (
  run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE RESTRICT,
  sku TEXT NOT NULL REFERENCES products(sku) ON DELETE RESTRICT,
  observation_date TEXT NOT NULL,
  presence INTEGER NOT NULL CHECK (presence IN (0, 1)),
  source_listing INTEGER CHECK (source_listing IN (0, 1) OR source_listing IS NULL),
  source_sitemap INTEGER CHECK (source_sitemap IN (0, 1) OR source_sitemap IS NULL),
  source_nuevo INTEGER CHECK (source_nuevo IN (0, 1) OR source_nuevo IS NULL),
  source_promo INTEGER CHECK (source_promo IN (0, 1) OR source_promo IS NULL),
  current_price REAL,
  original_price REAL,
  observation_complete INTEGER NOT NULL CHECK (observation_complete IN (0, 1)),
  raw_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (run_id, sku)
);

CREATE TABLE IF NOT EXISTS price_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sku TEXT NOT NULL REFERENCES products(sku) ON DELETE RESTRICT,
  canonical_id TEXT,
  observed_at TEXT NOT NULL,
  run_id TEXT REFERENCES runs(run_id) ON DELETE RESTRICT,
  previous_price REAL,
  new_price REAL,
  original_price REAL,
  unit_price_raw TEXT,
  change_type TEXT NOT NULL,
  promotion_raw TEXT,
  raw_json TEXT NOT NULL,
  source_file TEXT NOT NULL,
  source_sheet TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sku TEXT NOT NULL REFERENCES products(sku) ON DELETE RESTRICT,
  canonical_id TEXT,
  occurred_at TEXT NOT NULL,
  run_id TEXT REFERENCES runs(run_id) ON DELETE RESTRICT,
  event_type TEXT NOT NULL,
  old_value TEXT,
  new_value TEXT,
  evidence TEXT,
  source_file TEXT NOT NULL,
  source_sheet TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reviews (
  review_id TEXT PRIMARY KEY,
  sku TEXT REFERENCES products(sku) ON DELETE RESTRICT,
  review_date TEXT,
  issue_type TEXT NOT NULL,
  evidence TEXT,
  candidate_value TEXT,
  confidence REAL,
  suggested_action TEXT,
  manual_note TEXT,
  source_row_no INTEGER NOT NULL UNIQUE,
  source_sheet TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_metadata (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS migration_runs (
  migration_id TEXT PRIMARY KEY,
  source_master_path TEXT NOT NULL,
  source_master_sha256 TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  products_count INTEGER,
  localizations_count INTEGER,
  observations_count INTEGER,
  price_history_count INTEGER,
  events_count INTEGER,
  runs_count INTEGER,
  reviews_count INTEGER,
  validation_status TEXT,
  report_path TEXT
);

CREATE TABLE IF NOT EXISTS migration_source_issues (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  migration_id TEXT NOT NULL,
  source_sheet TEXT NOT NULL,
  source_row_no INTEGER NOT NULL,
  issue_code TEXT NOT NULL,
  source_key TEXT,
  raw_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  migration_id TEXT NOT NULL,
  source_sheet TEXT NOT NULL,
  source_row_no INTEGER NOT NULL,
  record_type TEXT NOT NULL,
  sku TEXT,
  canonical_id TEXT,
  raw_json TEXT NOT NULL,
  raw_hash TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(migration_id, source_sheet, source_row_no)
);

CREATE INDEX IF NOT EXISTS idx_products_status ON products(current_status_raw);
CREATE INDEX IF NOT EXISTS idx_price_history_sku_date ON price_history(sku, observed_at);
CREATE INDEX IF NOT EXISTS idx_events_sku_date ON events(sku, occurred_at);
CREATE INDEX IF NOT EXISTS idx_reviews_sku ON reviews(sku);

DROP VIEW IF EXISTS v_current_products_es;
DROP VIEW IF EXISTS v_current_products_zh;
DROP VIEW IF EXISTS v_db_current_skus;
CREATE VIEW v_current_products_es AS
  SELECT * FROM products WHERE current_status_raw = 'CURRENT';
CREATE VIEW v_current_products_zh AS
  SELECT p.sku, l.name, l.cat1, l.cat2, l.spec, l.description, l.details,
         p.name_es, p.product_url, p.current_price, p.current_status_raw
    FROM products p LEFT JOIN product_localizations l
      ON l.sku = p.sku AND l.language = 'zh'
   WHERE p.current_status_raw = 'CURRENT';
CREATE VIEW v_db_current_skus AS
  SELECT sku FROM products WHERE current_status_raw = 'CURRENT';
'''


def migrate(path):
    db = connect(path)
    try:
        db.executescript(DDL)
        db.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)", (SCHEMA_VERSION,))
        db.execute("INSERT OR REPLACE INTO schema_metadata(key,value) VALUES (?,?)", ("schema_family", SCHEMA_FAMILY))
        db.execute("INSERT OR REPLACE INTO schema_metadata(key,value) VALUES (?,?)", ("schema_version", SCHEMA_VERSION))
        db.commit()
    finally:
        db.close()


def inspect_schema(path: Path):
    """Classify a database before db-init mutates it.

    An old scaffold may contain a table named ``products`` but it does not
    have the V1 identity columns.  Such a file must be rebuilt from Master,
    not upgraded in place with ``CREATE TABLE IF NOT EXISTS``.
    """
    if not Path(path).exists() or Path(path).stat().st_size == 0:
        return "NEW"
    import sqlite3

    conn = sqlite3.connect(path)
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not tables:
            return "NEW"
        metadata = dict(conn.execute("SELECT key,value FROM schema_metadata").fetchall()) if "schema_metadata" in tables else {}
        family = metadata.get("schema_family")
        if family and family != SCHEMA_FAMILY:
            return "LEGACY"
        if "products" not in tables:
            return "LEGACY"
        columns = {row[1] for row in conn.execute("PRAGMA table_info(products)")}
        required = {"sku", "canonical_id", "source_sheet", "source_raw_json", "current_status_raw"}
        return "V1" if required <= columns else "LEGACY"
    finally:
        conn.close()
