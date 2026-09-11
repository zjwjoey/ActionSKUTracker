import sqlite3
c = sqlite3.connect("runtime/db/action_tracker.db")
rows = c.execute("""select p.official_sku,es.name,zh.name,es.cat1,zh.cat1,es.cat2,zh.cat2,zh.description
from products p join product_localizations es on es.official_sku=p.official_sku and es.language='es'
join product_localizations zh on zh.official_sku=p.official_sku and zh.language='zh'
where p.status='CURRENT' limit 10""").fetchall()
for row in rows:
    print(row)
