"""Backend-independent, content-bound backup sets. Never reads a live PRIMARY.

Input databases must already have a P1 BACKUP_ASSET_V1 sidecar. A sealed plan
travels inside the snapshot; a separately sealed receipt binds the backend ID.
Digests detect corruption, not a malicious operator with write access to both
the document and its digest. Keep receipts in access-controlled storage.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath

from .storage_foundation import StorageError, database_identity, read_database, sha256_file

PROFILES = {"CORE_BACKUP", "CURRENT_BUSINESS_BACKUP", "HISTORICAL_ARCHIVE_BACKUP"}
KINDS = {"verified_database", "projection", "image", "image_manifest", "source_evidence",
         "dictionary", "research_state", "run_manifest", "recovery_record", "historical_archive"}
SHA = re.compile(r"[0-9a-f]{64}\Z")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
ASSET_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}\Z")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def seal(value: dict) -> dict:
    if "document_sha256" in value:
        raise StorageError("DOCUMENT_ALREADY_SEALED")
    return {**value, "document_sha256": digest(value)}


def validate_seal(value: dict, contract: str | None = None) -> dict:
    if not isinstance(value, dict):
        raise StorageError("DOCUMENT_INVALID")
    body = {k: v for k, v in value.items() if k != "document_sha256"}
    if value.get("document_sha256") != digest(body):
        raise StorageError("DOCUMENT_DIGEST_MISMATCH")
    if contract and value.get("contract") != contract:
        raise StorageError("DOCUMENT_CONTRACT_MISMATCH")
    return value


def read_json(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (ValueError, OSError):
        raise StorageError("DOCUMENT_UNAVAILABLE_OR_INVALID") from None


def write_immutable(path: Path, value: dict):
    """Publish a fully fsynced document atomically without replacing any file."""
    path = safe_path(Path(path), file=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / (".new-" + uuid.uuid4().hex)
    try:
        with temporary.open("xb") as stream:
            stream.write(canonical(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if read_json(path) != value:
                raise StorageError("IMMUTABLE_RECORD_CONFLICT") from None
    finally:
        temporary.unlink(missing_ok=True)


def relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise StorageError("ASSET_PATH_INVALID")
    parts = value.split("/")
    if (PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute() or
            any(p in {"", ".", ".."} or p.rstrip(" .") != p for p in parts)):
        raise StorageError("ASSET_PATH_INVALID")
    for part in parts:
        if part.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(10)], *[f"LPT{i}" for i in range(10)]}:
            raise StorageError("ASSET_PATH_INVALID")
        if any(c in part for c in '<>"|?*\x00'):
            raise StorageError("ASSET_PATH_INVALID")
    return value


def safe_path(path: Path, *, file: bool = True) -> Path:
    """Reject links/reparse points along the complete existing path."""
    path = Path(os.path.abspath(path))
    for component in [path, *path.parents]:
        if component.exists() or component.is_symlink():
            st = component.lstat()
            if stat.S_ISLNK(st.st_mode) or getattr(st, "st_file_attributes", 0) & 0x400:
                raise StorageError("LINK_OR_REPARSE_POINT_BLOCKED")
    if file:
        if not path.is_file():
            raise StorageError("ASSET_UNAVAILABLE")
        if path.stat().st_nlink != 1:
            raise StorageError("HARDLINK_BLOCKED")
    return path.resolve()


def file_identity(path: Path) -> dict:
    path = safe_path(path)
    s = path.stat()
    return {"device": s.st_dev, "file_id": s.st_ino, "size_bytes": s.st_size,
            "mtime_ns": s.st_mtime_ns}


def synthetic_payload(recipe: dict) -> bytes:
    """A bounded, reproducible non-business fixture; not arbitrary source bytes."""
    if (not isinstance(recipe, dict) or set(recipe) != {"version", "label", "repeat"} or
            recipe["version"] != "ACTION_SYNTHETIC_V1" or
            not isinstance(recipe["label"], str) or not IDENTIFIER.fullmatch(recipe["label"]) or
            type(recipe["repeat"]) is not int or not 1 <= recipe["repeat"] <= 2000):
        raise StorageError("SYNTHETIC_RECIPE_INVALID")
    return ("ActionSKUTracker artificial fixture: " + recipe["label"] + "\n").encode("ascii") + bytes(range(256)) * recipe["repeat"]


def binding_id(asset: dict) -> str:
    return digest({k: asset.get(k) for k in ("asset_id", "root_id", "relative_path", "sku", "source_version")})


def select_images(index: dict, *, skus: list[str]) -> list[dict]:
    """Reuse P1's ASSET_INDEX_V1; no filename/SKU guessing or recursive scan."""
    if index.get("contract") != "ASSET_INDEX_V1" or len(set(skus)) != len(skus) or not skus:
        raise StorageError("IMAGE_SELECTION_INVALID")
    result = []
    for sku in skus:
        candidates = [a for a in index.get("assets", []) if a.get("sku") == sku and a.get("asset_type") == "IMAGE_MASTER"]
        if len(candidates) != 1 or candidates[0].get("qa_status") != "PASS":
            raise StorageError("IMAGE_ASSET_UNAVAILABLE_OR_AMBIGUOUS")
        item = {k: candidates[0].get(k) for k in ("asset_id", "root_id", "relative_path", "sha256", "sku", "source_version")}
        if not item["source_version"] or not item["sha256"]:
            raise StorageError("BUSINESS_SOURCE_BINDING_REQUIRED")
        result.append({**item, "kind": "image"})
    return result


def image_bindings(images: list[dict]) -> dict:
    return {"contract": "IMAGE_ASSET_BINDINGS_V1", "assets": [
        {k: a.get(k) for k in ("asset_id", "root_id", "relative_path", "sha256", "sku", "source_version")}
        for a in images if a.get("kind") == "image"]}


def _check_image_bindings(assets, locate):
    images = [a for a in assets if a["kind"] == "image"]
    manifests = [a for a in assets if a["kind"] == "image_manifest"]
    if not images and not manifests:
        return
    if not images or len(manifests) != 1:
        raise StorageError("PORTABLE_IMAGE_BINDINGS_REQUIRED")
    manifest = read_json(locate(manifests[0]))
    expected = image_bindings(images)
    if manifest != expected:
        raise StorageError("IMAGE_MANIFEST_BINDING_MISMATCH")


def bound_path(asset: dict, roots: dict[str, str]) -> Path:
    relative = relative_path(asset["relative_path"])
    if asset.get("root_id") not in roots:
        raise StorageError("ASSET_UNAVAILABLE")
    root = safe_path(Path(roots[asset["root_id"]]), file=False)
    path = safe_path(root / relative)
    if not path.is_relative_to(root):
        raise StorageError("ASSET_OUTSIDE_ROOT")
    return path


def _database_check(path: Path, p1: dict) -> dict:
    if (p1.get("contract") != "BACKUP_ASSET_V1" or p1.get("integrity") != "PASS" or
            p1.get("foreign_keys") != "PASS" or not p1.get("commit_id") or
            p1.get("schema_family") != "ACTION_SQLITE_DATA" or
            p1.get("schema_version") != "2.0.0" or
            p1.get("database_role") not in {"PRIMARY", "SHADOW"}):
        raise StorageError("P1_VERIFIED_BACKUP_REQUIRED")
    if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm")):
        raise StorageError("BACKUP_HAS_DATABASE_SIDECARS")
    try:
        with read_database(path, immutable=True) as db:
            identity = database_identity(db)
            integrity = [r[0] for r in db.execute("PRAGMA integrity_check")]
            foreign_keys = db.execute("PRAGMA foreign_key_check").fetchall()
            row = db.execute("SELECT * FROM export_sync WHERE commit_id=?", (p1["commit_id"],)).fetchone()
            export = dict(row) if row else {}
    except sqlite3.Error:
        raise StorageError("DATABASE_VALIDATION_FAILED") from None
    if integrity != ["ok"] or foreign_keys:
        raise StorageError("DATABASE_INTEGRITY_FAILED")
    if identity["commit_id"] != p1["commit_id"] or any(
            identity["metadata"].get(k) != p1.get(k)
            for k in ("schema_family", "schema_version", "database_role")):
        raise StorageError("DATABASE_IDENTITY_MISMATCH")
    return {"identity": identity, "export_sync": export, "integrity": "PASS", "foreign_keys": "PASS"}


def make_plan(*, backup_set_id: str, run_id: str, profile: str, roots: dict[str, str],
              assets: list[dict], repository: dict, config_version: str,
              recovery_owner: str, retention: dict, dependencies: list[str] | None = None,
              max_logical_bytes: int = 8 * 1024**3) -> dict:
    """Hash only an explicit selection. No recursive source discovery or live DB."""
    if not IDENTIFIER.fullmatch(backup_set_id) or not IDENTIFIER.fullmatch(run_id):
        raise StorageError("BACKUP_ID_INVALID")
    if profile not in PROFILES or not recovery_owner or not config_version:
        raise StorageError("BACKUP_PROFILE_OR_RESPONSIBILITY_MISSING")
    retention_fields = {"policy_id", "recovery_required", "reference_status", "retain_until", "release_approval_id", "owner"}
    if (not isinstance(retention, dict) or set(retention) - retention_fields or not retention.get("policy_id")):
        raise StorageError("RETENTION_POLICY_REQUIRED")
    if (set(repository) != {"repository_id", "config_sha256", "provider"} or
            not repository.get("repository_id") or not SHA.fullmatch(repository.get("config_sha256", ""))):
        raise StorageError("REPOSITORY_IDENTITY_REQUIRED")
    if not assets:
        raise StorageError("EMPTY_BACKUP_SET")
    if type(max_logical_bytes) is not int or max_logical_bytes <= 0:
        raise StorageError("BACKUP_BUDGET_INVALID")
    selected, seen_paths, seen_ids, seen_files = [], set(), set(), set()
    total = 0
    for input_asset in assets:
        # Explicit fields prevent accidental credentials/config dumps entering a manifest.
        item = {k: input_asset.get(k) for k in ("asset_id", "root_id", "relative_path", "kind",
                                               "sha256", "sku", "source_version", "projection_name",
                                               "retention_owner", "synthetic_recipe")}
        if (not isinstance(item["asset_id"], str) or not ASSET_ID.fullmatch(item["asset_id"]) or
                item["kind"] not in KINDS or not isinstance(item["sha256"], str) or
                not SHA.fullmatch(item["sha256"])):
            raise StorageError("ASSET_BINDING_INVALID")
        if not isinstance(item["root_id"], str) or not IDENTIFIER.fullmatch(item["root_id"]):
            raise StorageError("ROOT_ID_INVALID")
        item["relative_path"] = relative_path(item["relative_path"])
        item["snapshot_path"] = "assets/" + item["root_id"] + "/" + item["relative_path"]
        item["binding_id"] = binding_id(item)
        # Do not silently transfer private credential/browser/config payloads.
        if any(p.lower() in {".env", "cookies", "credentials", "browser_profile", ".git"}
               or p.lower().endswith((".clixml", ".pem", ".key")) for p in item["relative_path"].split("/")):
            raise StorageError("PRIVATE_ASSET_BLOCKED")
        if item["kind"] in {"image", "source_evidence"} and (not item["sku"] or not item["source_version"]):
            raise StorageError("BUSINESS_SOURCE_BINDING_REQUIRED")
        if profile == "HISTORICAL_ARCHIVE_BACKUP" and not item["retention_owner"]:
            raise StorageError("ARCHIVE_RESPONSIBILITY_REQUIRED")
        path = bound_path(item, roots)
        with path.open("rb") as probe:
            sqlite_file = probe.read(16) == b"SQLite format 3\x00"
        if sqlite_file and item["kind"] != "verified_database":
            raise StorageError("DATABASE_REQUIRES_P1_BACKUP_KIND")
        identity = file_identity(path)
        physical = (identity["device"], identity["file_id"])
        if (item["snapshot_path"].casefold() in seen_paths or item["binding_id"] in seen_ids or
                physical in seen_files):
            raise StorageError("DUPLICATE_ASSET_OR_PATH")
        seen_paths.add(item["snapshot_path"].casefold()); seen_ids.add(item["binding_id"]); seen_files.add(physical)
        total += identity["size_bytes"]
        if total > max_logical_bytes:
            raise StorageError("BACKUP_LOGICAL_BUDGET_EXCEEDED")
        if sha256_file(path) != item["sha256"] or file_identity(path) != identity:
            raise StorageError("ASSET_HASH_OR_IDENTITY_CHANGED")
        item.update(size_bytes=identity["size_bytes"], source_identity=identity)
        if item["kind"] == "verified_database":
            p1 = read_json(Path(str(path) + ".asset.json"))
            if (not p1.get("code_version") or p1.get("code_version") == "unknown" or
                    Path(p1.get("backup_path", "")).resolve() != path or
                    Path(p1.get("source", "")).resolve() == path or
                    p1.get("sha256") != item["sha256"] or p1.get("size_bytes") != item["size_bytes"]):
                raise StorageError("P1_BACKUP_BINDING_MISMATCH")
            item["p1"] = {k: p1.get(k) for k in ("contract", "run_id", "sha256", "size_bytes", "created_at",
                        "code_version", "integrity", "foreign_keys", "schema_family", "schema_version",
                        "database_role", "commit_id", "owner_task")}
            item["database_check"] = _database_check(path, item["p1"])
        selected.append(item)
    _check_image_bindings(selected, lambda a: bound_path(a, roots))
    dbs = [a for a in selected if a["kind"] == "verified_database"]
    if profile == "CORE_BACKUP":
        if len(dbs) != 1:
            raise StorageError("CORE_REQUIRES_ONE_P1_BACKUP")
        sync = dbs[0]["database_check"]["export_sync"]
        projections = [a for a in selected if a["kind"] == "projection"]
        if len(projections) != 3 or {a["projection_name"] for a in projections} != {"master", "known", "offline"}:
            raise StorageError("CORE_PROJECTIONS_INCOMPLETE")
        for projection in projections:
            key = projection["projection_name"]
            if (sync.get("status") != "SUCCESS" or sync.get(key + "_status") != "SUCCESS" or
                    sync.get(key + "_sha256") != projection["sha256"]):
                raise StorageError("CORE_PROJECTION_COMMIT_MISMATCH")
        if not all(any(a["kind"] == kind for a in selected) for kind in ("run_manifest", "recovery_record")):
            raise StorageError("CORE_RECOVERY_EVIDENCE_INCOMPLETE")
    return seal({"contract": "BACKUP_SET_PLAN_V1", "backup_set_id": backup_set_id, "run_id": run_id,
                 "created_at": now(), "profile": profile, "source_roots": sorted({a["root_id"] for a in selected}),
                 "assets": selected, "logical_bytes": total, "repository": repository,
                 "config_version": config_version, "recovery_owner": recovery_owner,
                 "retention": retention, "dependencies": dependencies or [],
                 "code_versions": sorted({a["p1"]["code_version"] for a in dbs}),
                 "database_identities": [a["database_check"]["identity"] for a in dbs],
                 "upload_status": "NOT_STARTED", "verification_status": "NOT_STARTED"})


def _validate_plan(plan):
    validate_seal(plan, "BACKUP_SET_PLAN_V1")
    if plan.get("profile") not in PROFILES or not IDENTIFIER.fullmatch(plan.get("backup_set_id", "")):
        raise StorageError("BACKUP_PLAN_INVALID")
    names = [relative_path(a["snapshot_path"]) for a in plan["assets"]]
    if len({p.casefold() for p in names}) != len(names):
        raise StorageError("DUPLICATE_ASSET_OR_PATH")
    if any(not p.startswith("assets/") for p in names):
        raise StorageError("BACKUP_PLAN_INVALID")
    ids = set()
    for asset in plan["assets"]:
        if (not ASSET_ID.fullmatch(asset.get("asset_id", "")) or binding_id(asset) in ids or
                not IDENTIFIER.fullmatch(asset.get("root_id", "")) or
                asset.get("snapshot_path") != "assets/" + asset["root_id"] + "/" + relative_path(asset["relative_path"]) or
                not SHA.fullmatch(asset.get("sha256", "")) or
                type(asset.get("size_bytes")) is not int or asset["size_bytes"] < 0 or
                asset.get("kind") not in KINDS):
            raise StorageError("BACKUP_PLAN_INVALID")
        ids.add(binding_id(asset))
        if asset["kind"] in {"image", "source_evidence"} and (not asset.get("sku") or not asset.get("source_version")):
            raise StorageError("BUSINESS_SOURCE_BINDING_REQUIRED")
        if asset.get("binding_id", binding_id(asset)) != binding_id(asset):
            raise StorageError("ASSET_BINDING_ID_MISMATCH")
        if plan["profile"] == "HISTORICAL_ARCHIVE_BACKUP" and not asset.get("retention_owner"):
            raise StorageError("ARCHIVE_RESPONSIBILITY_REQUIRED")
        if asset["kind"] == "verified_database":
            p1 = asset.get("p1", {})
            if (p1.get("contract") != "BACKUP_ASSET_V1" or p1.get("sha256") != asset["sha256"] or
                    p1.get("size_bytes") != asset["size_bytes"] or p1.get("integrity") != "PASS" or
                    p1.get("foreign_keys") != "PASS" or not p1.get("commit_id") or
                    not p1.get("code_version") or p1.get("code_version") == "unknown" or
                    p1.get("schema_family") != "ACTION_SQLITE_DATA" or p1.get("schema_version") != "2.0.0" or
                    p1.get("database_role") not in {"PRIMARY", "SHADOW"}):
                raise StorageError("P1_VERIFIED_BACKUP_REQUIRED")
    if plan["logical_bytes"] != sum(a["size_bytes"] for a in plan["assets"]):
        raise StorageError("BACKUP_PLAN_SIZE_MISMATCH")
    if not plan.get("recovery_owner") or not plan.get("retention", {}).get("policy_id"):
        raise StorageError("BACKUP_RESPONSIBILITY_REQUIRED")
    if plan["profile"] == "CORE_BACKUP":
        dbs = [a for a in plan["assets"] if a["kind"] == "verified_database"]
        projections = [a for a in plan["assets"] if a["kind"] == "projection"]
        if len(dbs) != 1:
            raise StorageError("CORE_REQUIRES_ONE_P1_BACKUP")
        if len(projections) != 3 or {a.get("projection_name") for a in projections} != {"master", "known", "offline"}:
            raise StorageError("CORE_PROJECTIONS_INCOMPLETE")
        sync = dbs[0].get("database_check", {}).get("export_sync", {})
        if (sync.get("commit_id") != dbs[0]["p1"]["commit_id"] or sync.get("status") != "SUCCESS" or
                any(sync.get(a["projection_name"] + "_status") != "SUCCESS" or
                    sync.get(a["projection_name"] + "_sha256") != a["sha256"] for a in projections)):
            raise StorageError("CORE_PROJECTION_COMMIT_MISMATCH")
        if not all(any(a["kind"] == kind for a in plan["assets"]) for kind in ("run_manifest", "recovery_record")):
            raise StorageError("CORE_RECOVERY_EVIDENCE_INCOMPLETE")
    return plan


def stage_plan(plan: dict, roots: dict[str, str], directory: Path, *, max_scratch_bytes: int,
               checkpoint=None) -> dict:
    _validate_plan(plan)
    directory = safe_path(directory, file=False)
    if directory.exists() and not (directory / "backup-set.json").is_file():
        raise StorageError("UNOWNED_STAGE_DIRECTORY")
    for root in roots.values():
        resolved = safe_path(Path(root), file=False)
        if directory.is_relative_to(resolved) or resolved.is_relative_to(directory):
            raise StorageError("STAGE_SOURCE_OVERLAP")
    needed = plan["logical_bytes"] + len(canonical(plan)) + 1
    if needed > max_scratch_bytes:
        raise StorageError("SCRATCH_BUDGET_EXCEEDED")
    directory.parent.mkdir(parents=True, exist_ok=True)
    remaining = sum(a["size_bytes"] for a in plan["assets"] if not (directory / a["snapshot_path"]).exists())
    if shutil.disk_usage(directory.parent).free < remaining + len(canonical(plan)) + 64 * 1024**2:
        raise StorageError("SCRATCH_DISK_SPACE_INSUFFICIENT")
    directory.mkdir(exist_ok=True)
    write_immutable(directory / "backup-set.json", plan)
    for asset in plan["assets"]:
        target = directory / asset["snapshot_path"]
        partial = target.parent / (".partial-" + binding_id(asset)[:24])
        if partial.exists():
            safe_path(partial, file=False)
            count = partial.stat().st_nlink
            owned_link = count == 2 and target.exists() and os.path.samefile(partial, target)
            if not partial.is_file() or (count != 1 and not owned_link):
                raise StorageError("UNSAFE_STAGE_PARTIAL")
            partial.unlink()
    expected = {"backup-set.json", *[a["snapshot_path"] for a in plan["assets"]]}
    for existing in directory.rglob("*"):
        safe_path(existing, file=existing.is_file())
        if existing.is_file() and existing.relative_to(directory).as_posix() not in expected:
            raise StorageError("UNEXPECTED_STAGED_FILE")
    for asset in plan["assets"]:
        if checkpoint:
            checkpoint()
        source = bound_path(asset, roots)
        if file_identity(source) != asset["source_identity"]:
            raise StorageError("SOURCE_CHANGED_SINCE_PLAN")
        with source.open("rb") as probe:
            if probe.read(16) == b"SQLite format 3\x00" and asset["kind"] != "verified_database":
                raise StorageError("DATABASE_REQUIRES_P1_BACKUP_KIND")
        if asset["kind"] == "verified_database":
            p1 = read_json(Path(str(source) + ".asset.json"))
            if (Path(p1.get("backup_path", "")).resolve() != source or
                    Path(p1.get("source", "")).resolve() == source or
                    any(p1.get(k) != v for k, v in asset["p1"].items())):
                raise StorageError("P1_BACKUP_BINDING_MISMATCH")
            if any(Path(str(source) + suffix).exists() for suffix in ("-wal", "-shm")):
                raise StorageError("BACKUP_HAS_DATABASE_SIDECARS")
        target = directory / asset["snapshot_path"]
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.parent / (".partial-" + binding_id(asset)[:24])
            # Resume may discard only this exact owned partial, after checking
            # that it is a regular, unlinked file inside the sealed stage.
            if temporary.exists():
                safe_path(temporary).unlink()
            try:
                with source.open("rb") as src, temporary.open("xb") as dst:
                    for chunk in iter(lambda: src.read(1024 * 1024), b""):
                        if checkpoint: checkpoint()
                        dst.write(chunk)
                    dst.flush(); os.fsync(dst.fileno())
                if sha256_file(temporary) != asset["sha256"]:
                    raise StorageError("STAGED_ASSET_MISMATCH")
                os.link(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        if (sha256_file(safe_path(target)) != asset["sha256"] or
                file_identity(source) != asset["source_identity"]):
            raise StorageError("STAGED_ASSET_MISMATCH")
    verify_restored(plan, directory)
    return {"status": "STAGED", "logical_bytes": needed, "files": len(expected), "directory": str(directory)}


def verify_restored(plan: dict, directory: Path) -> dict:
    _validate_plan(plan)
    directory = safe_path(directory, file=False)
    if read_json(directory / "backup-set.json") != plan:
        raise StorageError("RESTORED_MANIFEST_MISMATCH")
    expected = {"backup-set.json", *[a["snapshot_path"] for a in plan["assets"]]}
    observed = set()
    for path in directory.rglob("*"):
        safe_path(path, file=path.is_file())
        if path.is_file():
            observed.add(path.relative_to(directory).as_posix())
    if observed != expected:
        raise StorageError("RESTORED_ASSET_SET_MISMATCH")
    _check_image_bindings(plan["assets"], lambda a: directory / a["snapshot_path"])
    results = []
    for asset in plan["assets"]:
        path = safe_path(directory / relative_path(asset["snapshot_path"]))
        if path.stat().st_size != asset["size_bytes"] or sha256_file(path) != asset["sha256"]:
            raise StorageError("RESTORED_ASSET_HASH_MISMATCH")
        with path.open("rb") as probe:
            if probe.read(16) == b"SQLite format 3\x00" and asset["kind"] != "verified_database":
                raise StorageError("DATABASE_REQUIRES_P1_BACKUP_KIND")
        check = {"asset_id": asset["asset_id"], "sha256": asset["sha256"], "status": "PASS",
                 "relative_path": asset["snapshot_path"]}
        if asset["kind"] == "verified_database":
            actual = _database_check(path, asset["p1"])
            if actual != asset["database_check"]:
                raise StorageError("RESTORED_DATABASE_PROJECTION_MISMATCH")
            check["database"] = actual
        results.append(check)
    return {"status": "ROUNDTRIP_PASS", "backup_set_id": plan["backup_set_id"],
            "plan_sha256": plan["document_sha256"], "checked_at": now(), "assets": results,
            "logical_bytes": plan["logical_bytes"], "production_switched": False,
            "path_mapping": {r: str(directory / "assets" / r) for r in plan["source_roots"]}}
