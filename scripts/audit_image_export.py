from pathlib import Path
from action_tracker.config import load_settings
from action_tracker.images.assets import ImageManifest
from action_tracker.database.repository import ProductionRepository
from action_tracker.database.integration import database_path

cfg = load_settings()
repo = ProductionRepository(database_path(cfg))
current = {str(r["sku"]): r for r in repo.load_current_export_records()}
manifest = ImageManifest(Path(cfg["images"]["manifest_path"]))
available = {sku for sku, rec in manifest.records.items() if rec.available}
deriv = Path(cfg["paths"]["images"]) / "derivatives" / "excel_250"
print({"current": len(current), "manifest_available": len(available),
       "current_manifest": len(set(current) & available),
       "current_derivatives": sum((deriv / f"{sku}.png").exists() for sku in current),
       "manifest_missing_current": sorted(set(current) - available)[:20],
       "derivative_missing_current": sorted(sku for sku in current if not (deriv / f"{sku}.png").exists())[:20]})
