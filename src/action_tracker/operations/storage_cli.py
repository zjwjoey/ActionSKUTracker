"""Explicit Storage Foundation commands; no global runtime directory creation."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .storage_foundation import (StorageError, database_identity, projection_audit,
                                read_database, rehearse_restore, retention_preview,
                                sha256_file, source_signature, storage_lock, verified_backup, resolve_asset,
                                image_asset_index, configured_roots)


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _write_new(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, default=str)


def code_evidence(data_root: Path) -> tuple[dict, bytes]:
    deployment = _json(data_root / "runtime/deployment/active.json")
    source = Path(deployment["source_checkout"])
    def git(*args):
        return subprocess.check_output(["git", "--no-optional-locks", "-C", str(source), *args])
    head = git("rev-parse", "HEAD").decode().strip()
    branch = git("branch", "--show-current").decode().strip()
    names = [v.decode("utf-8") for v in git("diff", "HEAD", "--name-only", "-z").split(b"\0") if v]
    safe = [n for n in names if n.startswith(("src/", "tests/", "scripts/", "docs/"))]
    patch = git("diff", "HEAD", "--binary", "--", *safe) if safe else b""
    if head != deployment["code_sha"]:
        raise StorageError("DEPLOYMENT_HEAD_MISMATCH")
    if branch != deployment.get("source_branch", "main"):
        raise StorageError("DEPLOYMENT_BRANCH_MISMATCH")
    import hashlib
    return {"deployment": deployment, "actual_head": head, "branch": branch,
            "tracked_dirty_paths": names, "patch_sha256": hashlib.sha256(patch).hexdigest(),
            "patch_paths": safe, "excluded_private_or_unknown_paths": sorted(set(names) - set(safe)),
            "untracked_files_preserved": False, "credentials_exported": False}, patch


def run_storage_command(args) -> dict:
    if args.storage_action == "resolve":
        if args.roots:
            roots = _json(args.roots)
        else:
            from ..config import load_settings
            roots = configured_roots(load_settings(project_root_override=args.data_root))
        return resolve_asset(_json(args.asset), roots)
    if args.storage_action == "retention-plan":
        keep = args.keep_latest
        if keep is None:
            keep = 7
            if args.data_root:
                from ..config import load_settings
                cfg = load_settings(project_root_override=args.data_root)
                keep = ((cfg.get("storage_foundation") or {}).get("backup_policy") or {}).get("keep_latest", 7)
        return retention_preview([_json(p) for p in args.manifest], keep_latest=keep)
    if args.storage_action == "image-index":
        result = image_asset_index(Path(args.manifest), Path(args.data_root))
        path = Path(args.output).resolve()
        if path.is_relative_to(Path(args.data_root).resolve()):
            raise StorageError("ASSET_INDEX_OUTPUT_MUST_BE_OUTSIDE_PRODUCTION")
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_new(path, result)
        return {"status": "PASS", "path": str(path), "assets": len(result["assets"]),
                "contract": result["contract"]}
    # Import only after CLI has selected the data root. No ensure_runtime_dirs().
    from ..config import load_settings
    from ..database.integration import database_path
    profile = getattr(args, "profile", None) or os.environ.get("ACTION_TRACKER_CONFIG_PROFILE")
    cfg = load_settings(project_root_override=args.data_root,
                        overlay_paths=[profile] if profile else [])
    root = Path(cfg["project_root"])
    db_path = database_path(cfg)
    projections = {"master": Path(cfg["paths"]["master"]),
                   "known": Path(cfg["paths"]["state"]) / "known_skus.csv",
                   "offline": Path(cfg["paths"]["state"]) / "offline_skus.csv"}
    if args.storage_action == "audit":
        return projection_audit(db_path, projections)
    output = Path(args.output).resolve()
    source_checkout = Path(_json(root / "runtime/deployment/active.json")["source_checkout"]).resolve()
    if output.is_relative_to(root.resolve()) or output.is_relative_to(source_checkout):
        raise StorageError("BASELINE_OUTPUT_MUST_BE_OUTSIDE_PRODUCTION")
    output.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(output).free < 2 * db_path.stat().st_size + 128 * 1024**2:
        raise StorageError("RESTORE_DISK_BUDGET_EXCEEDED")
    run_id = "storage-baseline-" + uuid.uuid4().hex
    with storage_lock(Path(cfg["paths"]["state"]), run_id=run_id):
        before = projection_audit(db_path, projections)
        if before.get("status") != "PASS":
            raise StorageError("BASELINE_PROJECTION_BLOCKED")
        code, patch = code_evidence(root)
        config_sha = sha256_file(root / "config/settings.yaml")
        context = {"contract": "STORAGE_BASELINE_CONTEXT_V1", "source_database": str(db_path),
                   "commit_id": before["identity"]["commit_id"], "code": code,
                   "config_evidence": cfg.get("_config_evidence"), "config_sha256": config_sha}
        context_path = output / "baseline_context.json"
        if context_path.exists():
            if _json(context_path) != context:
                raise StorageError("BASELINE_EXISTING_CONTEXT_CHANGED")
        else:
            _write_new(context_path, context)
        backup = output / "primary.sqlite3"
        asset_path = Path(str(backup) + ".asset.json")
        reused = backup.exists()
        if reused:
            if not asset_path.is_file():
                raise StorageError("BASELINE_EXISTING_BACKUP_UNVERIFIED")
            manifest = _json(asset_path)
            if (manifest.get("contract") != "BACKUP_ASSET_V1" or
                    manifest.get("source") != str(db_path.resolve()) or
                    manifest.get("backup_path") != str(backup) or
                    manifest.get("integrity") != "PASS" or manifest.get("foreign_keys") != "PASS" or
                    any(manifest.get(k) != before["identity"]["metadata"].get(k)
                        for k in ("schema_family", "schema_version", "database_role")) or
                    manifest.get("size_bytes") != backup.stat().st_size or
                    manifest.get("commit_id") != before["identity"]["commit_id"] or
                    manifest.get("code_version") != code["actual_head"] + ":" + code["patch_sha256"] or
                    manifest.get("source_signature") != source_signature(db_path) or
                    sha256_file(backup) != manifest["sha256"]):
                raise StorageError("BASELINE_EXISTING_BACKUP_STALE")
        else:
            manifest = verified_backup(db_path, backup, run_id=run_id,
                                       code_version=code["actual_head"] + ":" + code["patch_sha256"])
        after = projection_audit(db_path, projections)
        final_code, _ = code_evidence(root)
        if (after != before or final_code != code or
                sha256_file(root / "config/settings.yaml") != config_sha or
                manifest["commit_id"] != after["identity"]["commit_id"] or
                manifest.get("source_signature") != source_signature(db_path)):
            raise StorageError("BASELINE_SOURCE_CHANGED")
        if not (output / "production_code.json").exists():
            _write_new(output / "production_code.json", code)
            (output / "production_source_changes.patch").write_bytes(patch)
    # Expensive restore checks run after releasing the production writer lock.
    restore_report = output / "restore_validation.json"
    if restore_report.exists():
        restore = _json(restore_report)
        if (restore.get("backup_sha256") != manifest["sha256"] or restore.get("status") != "PASS" or
                restore.get("commit_id") != manifest["commit_id"] or
                restore.get("integrity") != "PASS" or restore.get("foreign_keys") != "PASS" or
                restore.get("permanent_restore_copy_created") is not False):
            raise StorageError("BASELINE_EXISTING_RESTORE_UNVERIFIED")
    else:
        restore = rehearse_restore(backup, manifest, output)
        _write_new(restore_report, restore)
    result = {"status": "PASS", "scope": "BASELINE_AND_ISOLATED_RESTORE_NOT_DEPLOYMENT",
              "backup": manifest, "projections": before, "restore": restore, "reused": reused,
              "config_sha256": config_sha, "production_head": code["actual_head"],
              "production_patch_sha256": code["patch_sha256"],
              "created_at": datetime.now(timezone.utc).isoformat(), "business_writes": 0}
    # A repeat baseline keeps previous evidence rather than replacing it.
    _write_new(output / ("acceptance-" + run_id + ".json"), result)
    return result
