"""Generate/reuse the configured 250px white-background Excel derivatives."""
from pathlib import Path
import sqlite3
from PIL import Image
from action_tracker.images.derivatives import ImageDerivativeService

ROOT = Path(__file__).resolve().parents[1]
db = sqlite3.connect(ROOT / "runtime" / "db" / "action_tracker.db")
skus = [str(r[0]) for r in db.execute("select official_sku from products where status='CURRENT' order by official_sku")]
db.close()
assets = ROOT / "runtime" / "images" / "assets"
derivative_root = ROOT / "runtime" / "images" / "derivatives"
derivatives = derivative_root / "excel_250"
service = ImageDerivativeService(derivative_root)
generated = reused = missing = invalid = 0
for sku in skus:
    master = assets / sku / "master.png"
    target = derivatives / f"{sku}.png"
    if not master.exists():
        missing += 1
        continue
    if target.exists():
        reused += 1
        continue
    service.excel_250(master, sku)
    generated += 1
for sku in skus:
    target = derivatives / f"{sku}.png"
    if not target.exists():
        continue
    try:
        with Image.open(target) as im:
            if im.size != (250, 250) or im.mode != "RGB":
                invalid += 1
    except Exception:
        invalid += 1
print({"current_skus": len(skus), "generated": generated, "reused": reused, "missing_master": missing, "invalid_derivatives": invalid})
