from action_tracker.database.connection import connect
from action_tracker.database.schema import migrate, migrate_v2
from action_tracker.database.repository import import_baseline

def test_schema_creates_required_tables(tmp_path):
    path = tmp_path / 'action.db'
    migrate(path)
    with connect(path) as db:
        names = {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
    assert {'products','product_observations','price_history','event_history','translations','image_map','runs','sync_queue','schema_migrations'} <= names

def test_baseline_import_is_idempotent(tmp_path):
    db = tmp_path / 'action.db'
    records = {'1': {'canonical_id':'ACT0000001','name_es':'Uno','current_price':1.0}}
    assert import_baseline(db, records, '2026-08-10') == 1
    assert import_baseline(db, records, '2026-08-10') == 1
    with connect(db) as conn:
        assert conn.execute('select count(*) from products').fetchone()[0] == 1


def test_v2_migration_accepts_active_immutable_patch_schema(tmp_path):
    """A copied PRIMARY DB may predate this branch's additive DDL.

    The active extraction system stores patch events with ``occurred_at``
    and has a richer patch row.  Migration must preserve that schema rather
    than failing while creating an index for the legacy ``created_at`` shape.
    """
    path = tmp_path / "active-primary.sqlite"
    migrate(path)
    with connect(path) as db:
        db.executescript(
            """
            CREATE TABLE schema_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_metadata(key,value) VALUES ('database_role','PRIMARY');
            CREATE TABLE localization_patches (
                patch_id TEXT PRIMARY KEY, parent_patch_id TEXT,
                official_sku TEXT NOT NULL, language TEXT NOT NULL,
                field_name TEXT NOT NULL, old_value TEXT, new_value TEXT,
                source_hash TEXT NOT NULL, reason TEXT NOT NULL,
                created_by TEXT NOT NULL, created_at TEXT NOT NULL, revision INTEGER NOT NULL
            );
            CREATE TABLE localization_patch_events (
                event_id TEXT PRIMARY KEY, patch_id TEXT NOT NULL,
                event_type TEXT NOT NULL, actor TEXT NOT NULL,
                reason TEXT, event_json TEXT NOT NULL, occurred_at TEXT NOT NULL
            );
            """
        )
    migrate_v2(path, role="PRIMARY")
    with connect(path) as db:
        assert db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='localization_field_provenance'"
        ).fetchone()
        indexes = {row[1] for row in db.execute("PRAGMA index_list(localization_patch_events)")}
        assert "idx_localization_patch_events_patch" in indexes
