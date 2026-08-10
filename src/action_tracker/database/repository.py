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
        for sku, r in records.items():
            cid = r.get('canonical_id') or f'ACT{sku.zfill(7)}'
            db.execute('''INSERT INTO products(canonical_id,official_sku,name_es,name_zh,current_price,original_price,unit_price_raw,raw_badges,status,consecutive_missing,product_url,image_url,first_seen_at,last_seen_at,last_checked_at,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?, 'ACTIVE',0,?,?,?,?,?,?,?)
              ON CONFLICT(canonical_id) DO UPDATE SET name_es=excluded.name_es,name_zh=excluded.name_zh,current_price=excluded.current_price,original_price=excluded.original_price,unit_price_raw=excluded.unit_price_raw,raw_badges=excluded.raw_badges,status='ACTIVE',consecutive_missing=0,product_url=excluded.product_url,image_url=excluded.image_url,last_seen_at=excluded.last_seen_at,last_checked_at=excluded.last_checked_at,updated_at=excluded.updated_at''',
              (cid,sku,r.get('name_es'),r.get('name_zh'),r.get('current_price'),r.get('original_price'),r.get('unit_price'),r.get('raw_tags'),r.get('product_url'),r.get('image_url'),r.get('first_seen') or observed_at,observed_at,observed_at,now,now))
            db.execute('''INSERT OR IGNORE INTO product_observations(run_id,observation_date,canonical_id,official_sku,sitemap_seen,listing_seen,current_price,original_price,raw_json) VALUES(?,?,?,?,1,1,?,?,?)''',
              (f'BASELINE_{observed_at}',observed_at,cid,sku,r.get('current_price'),r.get('original_price'),json.dumps(r,ensure_ascii=False,default=str)))
        db.execute("INSERT OR REPLACE INTO runs(run_id,run_date,status,qa_state,dry_run,started_at,ended_at,schema_version) VALUES(?,?, 'BASELINE_IMPORTED','PASS',0,?,?, '1.0.0')",(f'BASELINE_{observed_at}',observed_at,now,now))
    return len(records)
