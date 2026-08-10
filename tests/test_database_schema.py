from action_tracker.database.connection import connect
from action_tracker.database.schema import migrate
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
