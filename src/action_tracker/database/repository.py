from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from .connection import connect
from .schema import migrate

def import_baseline(path: Path, records: dict[str, dict], observed_at: str) -> int:
    """Single-writer baseline import. Existing products are upserted, never deleted."""
    migrate(path)
    now = datetime.now().isoformat(timespec='seconds')
    with connect(path) as db:
        for source_row_no, (sku, r) in enumerate(records.items(), start=1):
            cid = r.get('canonical_id') or f'ACT{sku.zfill(7)}'
            raw = json.dumps(r, ensure_ascii=False, default=str)
            db.execute('''INSERT INTO products(sku,canonical_id,name_es,current_price,source_sheet,source_row_no,source_raw_json,current_status_raw,first_seen_at,last_seen_at,product_url,image_url,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(sku) DO UPDATE SET canonical_id=excluded.canonical_id,name_es=excluded.name_es,current_price=excluded.current_price,current_status_raw='CURRENT',last_seen_at=excluded.last_seen_at,product_url=excluded.product_url,image_url=excluded.image_url,updated_at=excluded.updated_at''',
              (sku,cid,r.get('name_es'),r.get('current_price'),'BASELINE',source_row_no,raw,'CURRENT',r.get('first_seen') or observed_at,observed_at,r.get('product_url'),r.get('image_url'),now,now))
            db.execute('''INSERT OR IGNORE INTO product_observations(run_id,observation_date,canonical_id,official_sku,sitemap_seen,listing_seen,current_price,original_price,raw_json) VALUES(?,?,?,?,1,1,?,?,?)''',
              (f'BASELINE_{observed_at}',observed_at,cid,sku,r.get('current_price'),r.get('original_price'),json.dumps(r,ensure_ascii=False,default=str)))
        db.execute("INSERT OR REPLACE INTO runs_legacy(run_id,run_date,status,qa_state,dry_run,started_at,ended_at,schema_version) VALUES(?,?, 'BASELINE_IMPORTED','PASS',0,?,?, '1.0.0')",(f'BASELINE_{observed_at}',observed_at,now,now))
    return len(records)
