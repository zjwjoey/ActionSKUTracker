from __future__ import annotations
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
