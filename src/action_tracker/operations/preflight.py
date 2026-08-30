"""Production environment checks shared by the operations runner."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from ..database.production import ProductionDatabaseError, validate_production_database


def production_preflight(cfg: dict[str, Any], db_path: Path, report_root: Path) -> dict[str, Any]:
    storage_mode = str((cfg.get("storage") or {}).get("mode") or "").upper()
    if storage_mode != "SQLITE_PRIMARY":
        raise ProductionDatabaseError("PREFLIGHT_STORAGE_MODE_MUST_BE_SQLITE_PRIMARY")
    database = validate_production_database(Path(db_path))
    if database.get("integrity") != "PASS" or database.get("foreign_keys") != "PASS":
        raise ProductionDatabaseError("PREFLIGHT_DATABASE_VALIDATION_FAILED")

    paths = cfg.get("paths") or {}
    runtime = Path(cfg["project_root"]) / "runtime"
    writable = {
        "runtime": _writable_probe(runtime),
        "backup": _writable_probe(Path(paths["backups"])),
        "reports": _writable_probe(Path(report_root)),
    }
    if not all(writable.values()):
        raise RuntimeError("PREFLIGHT_PATH_NOT_WRITABLE")

    disk_paths = {
        "database": Path(db_path),
        "images": Path(paths.get("images") or runtime / "images"),
        "exports": Path(paths.get("exports") or runtime / "exports"),
        "master": Path(paths.get("master") or runtime / "master"),
    }
    min_free = int((cfg.get("operations") or {}).get("min_free_disk_bytes", 2 * 1024**3))
    disks: dict[str, dict[str, int | bool]] = {}
    seen: dict[str, dict[str, int | bool]] = {}
    for name, path in disk_paths.items():
        probe = Path(path)
        if probe.is_file():
            probe = probe.parent
        key = str(probe.resolve().anchor or probe.resolve())
        if key not in seen:
            free = int(shutil.disk_usage(probe).free)
            seen[key] = {"free_bytes": free, "min_free_bytes": min_free, "ok": free >= min_free}
        disks[name] = seen[key]
    if not all(bool(value["ok"]) for value in seen.values()):
        raise RuntimeError("PREFLIGHT_DISK_SPACE_LOW")

    snapshot = _safe_config_snapshot(cfg)
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "status": "PASS",
        "database": database,
        "storage_mode": storage_mode,
        "writable": writable,
        "disks": disks,
        "config_snapshot": snapshot,
        "config_hash": hashlib.sha256(encoded).hexdigest(),
    }


def _writable_probe(directory: Path) -> bool:
    directory = Path(directory)
    if not directory.exists() or not directory.is_dir():
        return False
    probe = directory / f".preflight-{os.getpid()}-{uuid.uuid4().hex}.tmp"
    try:
        with probe.open("w", encoding="utf-8") as handle:
            handle.write("ok\n")
            handle.flush()
            os.fsync(handle.fileno())
        return True
    except OSError:
        return False
    finally:
        probe.unlink(missing_ok=True)


def _safe_config_snapshot(cfg: dict[str, Any]) -> dict[str, Any]:
    run = cfg.get("run") or {}
    image = cfg.get("images") or {}
    knowledge = cfg.get("knowledge") or {}
    dictionary = cfg.get("scoped_dictionary") or {}
    translation = cfg.get("translation") or {}
    ai = cfg.get("ai") or {}
    return {
        "storage_mode": str((cfg.get("storage") or {}).get("mode") or ""),
        "database_path": str((cfg.get("storage") or {}).get("db_path") or ""),
        "image_enabled": bool(run.get("image_download_enabled", image.get("enabled", False))),
        "knowledge_apply_enabled": bool(knowledge.get("production_apply_enabled", False)),
        "scoped_dictionary_enabled": bool(dictionary.get("enabled", False)),
        "ai_enabled": bool(translation.get("ai_enabled", False) or ai.get("translation_provider")),
        "provider": str(translation.get("provider") or ai.get("translation_provider") or ""),
        "model": str(translation.get("model") or ai.get("model") or ""),
        "auto_approval_enabled": bool(translation.get("auto_approval_enabled", False)),
    }
