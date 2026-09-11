"""Create a local, read-only URL index for missing official second-level categories."""
from html import escape
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "runtime" / "db" / "action_tracker.db"
OUT = ROOT / "runtime" / "temp" / "cat2_backlog_20260907.html"

conn = sqlite3.connect(DB)
rows = conn.execute(
    """select p.official_sku, p.name_es, p.product_url
       from products p left join product_localizations es
         on es.official_sku=p.official_sku and es.language='es'
       where p.status='CURRENT' and trim(coalesce(es.cat2,''))=''
       order by p.official_sku"""
).fetchall()
conn.close()

items = "\n".join(
    f'<li data-sku="{escape(str(sku), quote=True)}" data-url="{escape(str(url or ""), quote=True)}">'
    f'{escape(str(sku))} — {escape(str(name or ""))}</li>'
    for sku, name, url in rows
)
html = f'''<!doctype html><meta charset="utf-8"><title>cat2 backlog</title>
<h1>Missing cat2: {len(rows)}</h1><ol id="items">{items}</ol>'''
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(html, encoding="utf-8")
print(f"{len(rows)} rows -> {OUT}")
print("SKUS=" + ",".join(str(r[0]) for r in rows))
