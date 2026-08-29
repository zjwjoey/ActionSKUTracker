from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..exporting.profiles import load_profile
from ..exporting.service import resolve_formal_source
from .sync import ImageSyncService


def sync_formal_current(cfg: dict[str, Any], *, export_date: str, run_id: str | None = None) -> dict[str, Any]:
    """Synchronize images for a formal CURRENT source only."""
    profile = load_profile(cfg, language="es", no_images=True)
    source = resolve_formal_source(cfg, export_date=export_date, requested_run_id=run_id, profile=profile)
    image_cfg = cfg.get("images") or {}
    root = Path(cfg["paths"]["images"])
    service = ImageSyncService(
        asset_root=_resolve(cfg, image_cfg.get("asset_root"), root / "assets"),
        staging_root=_resolve(cfg, image_cfg.get("staging_root"), root / "staging"),
        manifest_path=_resolve(cfg, image_cfg.get("manifest_path"), root / "manifests" / "image_manifest.csv"),
        timeout_seconds=int(image_cfg.get("timeout_seconds", 20)),
        max_retries=int(image_cfg.get("max_retries", 3)),
        download_workers=int(image_cfg.get("download_workers", 1)),
    )
    rows = [{"sku": r.get("sku"), "canonical_id": r.get("canonical_id"), "image_url": r.get("image_url")} for r in source.records]
    result = service.sync(rows, run_id=source.run_id)
    report_path = root / "reports" / "image_sync" / f"{source.run_id}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    if str((cfg.get("storage") or {}).get("mode") or "EXCEL_PRIMARY").upper() == "SQLITE_PRIMARY":
        from ..database.integration import database_path
        from ..database.production import persist_image_manifest
        result["sqlite_image_sync"] = persist_image_manifest(database_path(cfg), service.manifest.path)
    report_path.write_text(json.dumps({**result, "source_kind": source.kind, "export_date": export_date}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result["report"] = str(report_path)
    return result


def image_status(cfg: dict[str, Any]) -> dict[str, Any]:
    image_cfg = cfg.get("images") or {}
    root = Path(cfg["paths"]["images"])
    from .assets import ImageManifest
    manifest = ImageManifest(_resolve(cfg, image_cfg.get("manifest_path"), root / "manifests" / "image_manifest.csv"))
    counts: dict[str, int] = {}
    for record in manifest.records.values():
        key = "AVAILABLE" if record.available else record.download_status
        counts[key] = counts.get(key, 0) + 1
    return {"total": len(manifest.records), "counts": counts, "manifest": str(manifest.path)}


def _resolve(cfg: dict[str, Any], raw: Any, fallback: Path) -> Path:
    if not raw:
        return fallback
    path = Path(str(raw))
    return path if path.is_absolute() else Path(cfg["project_root"]) / path
