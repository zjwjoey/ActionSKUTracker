from pathlib import Path
import sqlite3
import re
import sys
sys.stdout.reconfigure(encoding="utf-8")

root = Path(__file__).resolve().parents[1]
db = sqlite3.connect(root / "runtime" / "db" / "action_tracker.db")
rows = db.execute("""select p.official_sku,
       es.name,es.cat1,es.cat2,es.spec,es.description,es.details,
       zh.name,zh.cat1,zh.cat2,zh.spec,zh.description,zh.details
    from products p
    join product_localizations es on es.official_sku=p.official_sku and es.language='es'
    join product_localizations zh on zh.official_sku=p.official_sku and zh.language='zh'
    where p.status='CURRENT'""").fetchall()
db.close()
fields = ("name", "cat1", "cat2", "spec", "description", "details")
blank_by_field = {f: 0 for f in fields}
same_by_field = {f: 0 for f in fields}
no_hanzi_by_field = {f: 0 for f in fields}
any_blank = any_same = 0
spanish_residue = set()
spanish_word = re.compile(r"\b(?:para|con|sin|y|el|la|los|las|una?|de|del|producto|color|unidades|gramos|litros?|piezas?|varios|diferentes|nuevo|promoción|semanal|negro|blanco|rojo|azul)\b", re.I)
for row in rows:
    es = dict(zip(fields, row[1:7]))
    zh = dict(zip(fields, row[7:13]))
    row_blank = row_same = False
    for f in fields:
        zv = str(zh[f] or "").strip()
        ev = str(es[f] or "").strip()
        if not zv:
            blank_by_field[f] += 1; row_blank = True
        if ev and zv and zv == ev:
            same_by_field[f] += 1; row_same = True
        if zv and not re.search(r"[\u4e00-\u9fff]", zv):
            no_hanzi_by_field[f] += 1
        if zv and spanish_word.search(zv):
            spanish_residue.add(str(row[0]))
    any_blank += row_blank
    any_same += row_same
print({"current_skus": len(rows), "any_chinese_field_blank": any_blank,
       "blank_by_field": blank_by_field, "any_zh_field_equals_es": any_same,
       "same_by_field": same_by_field, "no_hanzi_by_field": no_hanzi_by_field,
       "possible_spanish_residue_skus": len(spanish_residue)})
