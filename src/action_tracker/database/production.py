"""SQLite V2 production transaction primitives.

The writer accepts a pre-computed CommitBundle. Collection and lifecycle code
remain outside this module; this module only persists an already QA-approved
bundle atomically.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .connection import connect
from .schema import migrate_v2


class ProductionDatabaseError(RuntimeError):
    """Production DB identity, baseline or transaction validation failure."""


@dataclass(frozen=True)
class CommitBundle:
    run_id: str
    observation_date: str
    qa_state: str
    current_products: tuple[dict[str, Any], ...] = ()
    localization_updates: tuple[dict[str, Any], ...] = ()
    lifecycle_updates: tuple[dict[str, Any], ...] = ()
    observations: tuple[dict[str, Any], ...] = ()
    price_events: tuple[dict[str, Any], ...] = ()
    event_events: tuple[dict[str, Any], ...] = ()
    review_rows: tuple[dict[str, Any], ...] = ()
    run_record: dict[str, Any] = field(default_factory=dict)
    snapshot_path: str | None = None
    snapshot_hash: str | None = None
    base_commit_id: str | None = None
    bundle_hash: str | None = None

    def resolved_hash(self) -> str:
        if self.bundle_hash:
            return self.bundle_hash
        payload = {
            "run_id": self.run_id,
            "observation_date": self.observation_date,
            "qa_state": self.qa_state,
            "current_products": self.current_products,
            "localization_updates": self.localization_updates,
            "lifecycle_updates": self.lifecycle_updates,
            "observations": self.observations,
            "price_events": self.price_events,
            "event_events": self.event_events,
            "review_rows": self.review_rows,
            "run_record": self.run_record,
            "snapshot_path": self.snapshot_path,
            "snapshot_hash": self.snapshot_hash,
            "base_commit_id": self.base_commit_id,
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class ProductionWriter:
    """Single-writer SQLite V2 commit implementation.

    The database role is checked on every commit. Shadow mode is the only
    enabled production-safe role for this first implementation; PRIMARY can be
    enabled explicitly once cutover acceptance is complete.
    """

    def __init__(self, path: Path, *, role: str = "SHADOW") -> None:
        self.path = Path(path)
        self.role = role
        if self.path.exists():
            try:
                with sqlite3.connect(self.path) as raw:
                    row = raw.execute("SELECT value FROM schema_metadata WHERE key='database_role'").fetchone()
            except sqlite3.OperationalError:
                row = None
            if row and str(row[0]) != role:
                raise ProductionDatabaseError("DB_ROLE_MISMATCH_REQUIRES_EXPLICIT_CUTOVER")
        migrate_v2(self.path, role=role)

    def commit(self, bundle: CommitBundle) -> str:
        if bundle.qa_state not in {"PASS", "PASS_PRESENCE_ONLY"}:
            raise ProductionDatabaseError("DB_COMMIT_QA_NOT_PASS")
        if not bundle.run_id or not bundle.observation_date:
            raise ProductionDatabaseError("DB_COMMIT_RUN_ID_OR_DATE_MISSING")
        now = datetime.now(timezone.utc).isoformat()
        commit_id = f"{bundle.observation_date}_{bundle.run_id}_{bundle.resolved_hash()[:12]}"
        with connect(self.path) as db:
            self._check_identity(db)
            existing = db.execute("SELECT commit_id FROM commit_batches WHERE run_id=?", (bundle.run_id,)).fetchone()
            if existing:
                if existing[0] != commit_id:
                    raise ProductionDatabaseError("DB_COMMIT_RUN_ALREADY_EXISTS_WITH_DIFFERENT_BUNDLE")
                return str(existing[0])
            latest = db.execute("SELECT commit_id FROM commit_batches WHERE status='COMMITTED' ORDER BY committed_at DESC LIMIT 1").fetchone()
            latest_id = str(latest[0]) if latest else None
            if bundle.base_commit_id is not None and bundle.base_commit_id != latest_id:
                raise ProductionDatabaseError("BASELINE_CHANGED_BEFORE_COMMIT")
            try:
                db.execute("BEGIN IMMEDIATE")
                self._insert_run(db, bundle, now)
                self._upsert_products(db, bundle.current_products, commit_id, now)
                self._upsert_localizations(db, bundle.localization_updates, commit_id, now)
                self._insert_observations(db, bundle.observations)
                self._upsert_lifecycle(db, bundle.lifecycle_updates, now)
                self._insert_prices(db, bundle.price_events, bundle.run_id)
                self._insert_events(db, bundle.event_events, bundle.run_id)
                self._insert_reviews(db, bundle.review_rows, bundle.run_id)
                db.execute(
                    "INSERT INTO run_evidence(run_id,snapshot_path,snapshot_hash,evidence_json) VALUES(?,?,?,?)",
                    (bundle.run_id, bundle.snapshot_path, bundle.snapshot_hash, json.dumps(bundle.run_record, ensure_ascii=False, sort_keys=True, default=str)),
                )
                db.execute(
                    "INSERT INTO commit_batches(commit_id,run_id,base_commit_id,bundle_hash,schema_version,started_at,committed_at,product_count,observation_count,price_event_count,event_count,snapshot_path,snapshot_hash,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (commit_id, bundle.run_id, bundle.base_commit_id, bundle.resolved_hash(), "2.0.0", now, now,
                     len(bundle.current_products), len(bundle.observations), len(bundle.price_events), len(bundle.event_events),
                     bundle.snapshot_path, bundle.snapshot_hash, "COMMITTED"),
                )
                db.execute("INSERT INTO export_sync(commit_id,status) VALUES(?, 'PENDING')", (commit_id,))
                self._validate_transaction(db, bundle, commit_id)
                db.commit()
            except Exception:
                db.rollback()
                raise
        return commit_id

    def _check_identity(self, db: sqlite3.Connection) -> None:
        values = {row[0]: row[1] for row in db.execute("SELECT key,value FROM schema_metadata")}
        if values.get("schema_family") != "ACTION_SQLITE_DATA" or values.get("schema_version") != "2.0.0":
            raise ProductionDatabaseError("DB_SCHEMA_IDENTITY_MISMATCH")
        if values.get("database_role") not in {"SHADOW", "PRIMARY"}:
            raise ProductionDatabaseError("DB_ROLE_INVALID")
        if values.get("database_role") == "PRIMARY" and self.role != "PRIMARY":
            raise ProductionDatabaseError("DB_PRIMARY_REQUIRES_PRIMARY_WRITER")

    @staticmethod
    def _insert_run(db: sqlite3.Connection, bundle: CommitBundle, now: str) -> None:
        r = bundle.run_record
        db.execute(
            "INSERT INTO runs(run_id,run_date,status,qa_state,dry_run,started_at,ended_at,schema_version) VALUES(?,?,?,?,?,?,?,?)",
            (bundle.run_id, bundle.observation_date, "COMMITTED", bundle.qa_state, int(bool(r.get("dry_run", False))),
             r.get("started_at") or now, r.get("finished_at") or now, "2.0.0"),
        )

    @staticmethod
    def _upsert_products(db: sqlite3.Connection, rows: Iterable[dict[str, Any]], commit_id: str, now: str) -> None:
        for r in rows:
            sku = str(r.get("official_sku") or r.get("sku") or "").strip()
            if not sku:
                raise ProductionDatabaseError("DB_PRODUCT_SKU_MISSING")
            cid = str(r.get("canonical_id") or f"ACT{sku.zfill(7)}")
            if r.get("_historical_minimal"):
                # Existing historical identities already hold their official
                # facts.  A minimal lifecycle-only row must never null out
                # those facts or reset badges/prices during an absence run.
                if db.execute("SELECT 1 FROM products WHERE official_sku=?", (sku,)).fetchone():
                    continue
            db.execute(
                """INSERT INTO products(canonical_id,official_sku,name_es,name_zh,current_price,original_price,unit_price_raw,raw_badges,
                action_new_badge,promotion_active,sustainable_badge,status,consecutive_missing,product_url,image_url,first_seen_at,
                last_seen_at,last_checked_at,source_hash,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(official_sku) DO UPDATE SET canonical_id=excluded.canonical_id,name_es=excluded.name_es,name_zh=excluded.name_zh,
                current_price=excluded.current_price,original_price=excluded.original_price,unit_price_raw=excluded.unit_price_raw,
                raw_badges=excluded.raw_badges,action_new_badge=excluded.action_new_badge,promotion_active=excluded.promotion_active,
                sustainable_badge=excluded.sustainable_badge,status=excluded.status,consecutive_missing=excluded.consecutive_missing,
                product_url=excluded.product_url,image_url=excluded.image_url,first_seen_at=excluded.first_seen_at,last_seen_at=excluded.last_seen_at,
                last_checked_at=excluded.last_checked_at,source_hash=excluded.source_hash,updated_at=excluded.updated_at""",
                (cid, sku, r.get("name_es"), r.get("name_zh"), r.get("current_price"), r.get("original_price"), r.get("unit_price_raw", r.get("unit_price")),
                 r.get("raw_badges", r.get("raw_tags")), int(bool(r.get("action_new_badge", r.get("is_new_badge", False)))),
                 int(bool(r.get("promotion_active", r.get("promotion", False)))), int(bool(r.get("sustainable_badge", r.get("sustainable", False)))),
                 r.get("status", "ACTIVE"), int(r.get("consecutive_missing", 0) or 0), r.get("product_url"), r.get("image_url"),
                 r.get("first_seen_at", r.get("first_seen")), r.get("last_seen_at", r.get("last_seen")), r.get("last_checked_at", now), r.get("source_hash"), now),
            )

    @staticmethod
    def _upsert_localizations(db: sqlite3.Connection, rows: Iterable[dict[str, Any]], commit_id: str, now: str) -> None:
        for r in rows:
            sku = str(r.get("official_sku") or r.get("sku") or "").strip()
            language = str(r.get("language") or "zh")
            db.execute(
                """INSERT INTO product_localizations(official_sku,language,name,cat1,cat2,spec,description,details,source,review_status,updated_at,last_commit_id)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(official_sku,language) DO UPDATE SET name=excluded.name,cat1=excluded.cat1,cat2=excluded.cat2,spec=excluded.spec,
                description=excluded.description,details=excluded.details,source=excluded.source,review_status=excluded.review_status,
                updated_at=excluded.updated_at,last_commit_id=excluded.last_commit_id""",
                (sku, language, r.get("name"), r.get("cat1"), r.get("cat2"), r.get("spec"), r.get("description"), r.get("details"),
                 r.get("source"), r.get("review_status"), now, commit_id),
            )

    @staticmethod
    def _insert_observations(db: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> None:
        for r in rows:
            state = str(r.get("presence_state") or "UNKNOWN")
            if state not in {"PRESENT", "ABSENT", "UNKNOWN"}:
                raise ProductionDatabaseError("DB_OBSERVATION_STATE_INVALID")
            db.execute(
                """INSERT INTO observations(run_id,official_sku,observation_date,presence_state,sitemap_present,listing_present,nuevo_present,
                promotion_present,observation_complete,absence_capable,current_price,source_flag) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (r.get("run_id"), r.get("official_sku") or r.get("sku"), r.get("observation_date"), state, r.get("sitemap_present"),
                 r.get("listing_present"), r.get("nuevo_present"), r.get("promotion_present"), int(bool(r.get("observation_complete", False))),
                 int(bool(r.get("absence_capable", False))), r.get("current_price"), r.get("source_flag")),
            )

    @staticmethod
    def _upsert_lifecycle(db: sqlite3.Connection, rows: Iterable[dict[str, Any]], now: str) -> None:
        for r in rows:
            sku = str(r.get("official_sku") or r.get("sku") or "").strip()
            cid = str(r.get("canonical_id") or f"ACT{sku.zfill(7)}")
            db.execute(
                """INSERT INTO lifecycle_state(official_sku,canonical_id,first_seen_date,last_seen_date,current_status,missing_count,last_missing_date,
                offline_date,last_state_observation_date,ever_offline,last_run_id,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(official_sku) DO UPDATE SET canonical_id=excluded.canonical_id,first_seen_date=excluded.first_seen_date,
                last_seen_date=excluded.last_seen_date,current_status=excluded.current_status,missing_count=excluded.missing_count,
                last_missing_date=excluded.last_missing_date,offline_date=excluded.offline_date,last_state_observation_date=excluded.last_state_observation_date,
                ever_offline=excluded.ever_offline,last_run_id=excluded.last_run_id,updated_at=excluded.updated_at""",
                (sku, cid, r.get("first_seen_date", r.get("first_seen")), r.get("last_seen_date", r.get("last_seen")), r.get("current_status", r.get("last_status", "ACTIVE")),
                 int(r.get("missing_count", r.get("consecutive_missing", 0)) or 0), r.get("last_missing_date"), r.get("offline_date"),
                 r.get("last_state_observation_date"), int(bool(r.get("ever_offline", False))), r.get("last_run_id"), now),
            )

    @staticmethod
    def _insert_prices(db: sqlite3.Connection, rows: Iterable[dict[str, Any]], run_id: str) -> None:
        for r in rows:
            key = str(r.get("event_key") or _event_key(run_id, r, "price"))
            db.execute(
                "INSERT OR IGNORE INTO price_history(canonical_id,official_sku,observed_at,old_price,new_price,change_type,run_id,event_key) VALUES(?,?,?,?,?,?,?,?)",
                (r.get("canonical_id"), r.get("official_sku") or r.get("sku"), r.get("observed_at") or r.get("date"), r.get("old_price"),
                 r.get("new_price"), r.get("change_type", r.get("direction", "CHANGE")), r.get("run_id", run_id), key),
            )

    @staticmethod
    def _insert_events(db: sqlite3.Connection, rows: Iterable[dict[str, Any]], run_id: str) -> None:
        for r in rows:
            key = str(r.get("event_key") or _event_key(run_id, r, "event"))
            db.execute(
                "INSERT OR IGNORE INTO event_history(canonical_id,official_sku,occurred_at,event_type,old_value,new_value,run_id,evidence,event_key) VALUES(?,?,?,?,?,?,?,?,?)",
                (r.get("canonical_id"), r.get("official_sku") or r.get("sku"), r.get("occurred_at") or r.get("date"), r.get("event_type"),
                 r.get("old_value"), r.get("new_value"), r.get("run_id", run_id), r.get("evidence"), key),
            )

    @staticmethod
    def _insert_reviews(db: sqlite3.Connection, rows: Iterable[dict[str, Any]], run_id: str) -> None:
        # Review rows are retained in the existing sync queue until a dedicated
        # review table is migrated; this keeps V2 additive and backward-safe.
        for r in rows:
            db.execute("INSERT INTO sync_queue(entity_type,entity_id,action,status) VALUES(?,?,?, 'PENDING')", ("review", str(r.get("review_id") or r.get("sku")), json.dumps({**r, "run_id": run_id}, ensure_ascii=False, default=str)))

    @staticmethod
    def _validate_transaction(db: sqlite3.Connection, bundle: CommitBundle, commit_id: str) -> None:
        if db.execute("SELECT COUNT(*) FROM commit_batches WHERE commit_id=?", (commit_id,)).fetchone()[0] != 1:
            raise ProductionDatabaseError("DB_COMMIT_BATCH_MISSING")
        if db.execute("SELECT COUNT(*) FROM runs WHERE run_id=?", (bundle.run_id,)).fetchone()[0] != 1:
            raise ProductionDatabaseError("DB_RUN_MISSING")
        orphan = db.execute("SELECT COUNT(*) FROM observations o LEFT JOIN products p ON p.official_sku=o.official_sku WHERE o.run_id=? AND p.official_sku IS NULL", (bundle.run_id,)).fetchone()[0]
        if orphan:
            raise ProductionDatabaseError("DB_OBSERVATION_ORPHAN")


def _event_key(run_id: str, row: dict[str, Any], kind: str) -> str:
    payload = json.dumps({"kind": kind, "run_id": run_id, **row}, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def database_status(path: Path) -> dict[str, Any]:
    """Return a compact, read-only production database status."""
    if not Path(path).exists():
        return {"exists": False, "path": str(path)}
    with connect(Path(path)) as db:
        metadata = {row[0]: row[1] for row in db.execute("SELECT key,value FROM schema_metadata")}
        try:
            latest = db.execute("SELECT commit_id,run_id,committed_at,status FROM commit_batches ORDER BY committed_at DESC LIMIT 1").fetchone()
            pending = db.execute("SELECT COUNT(*) FROM export_sync WHERE status != 'SUCCESS'").fetchone()[0]
            products = db.execute("SELECT COUNT(*) FROM products").fetchone()[0]
            lifecycle = db.execute("SELECT COUNT(*) FROM lifecycle_state").fetchone()[0]
        except sqlite3.OperationalError:
            return {"exists": True, "path": str(path), "metadata": metadata, "legacy_schema": True,
                    "latest_commit": None, "pending_export_sync": None,
                    "products": db.execute("SELECT COUNT(*) FROM products").fetchone()[0], "lifecycle": None}
        return {"exists": True, "path": str(path), "metadata": metadata, "latest_commit": dict(latest) if latest else None,
                "pending_export_sync": pending, "products": products, "lifecycle": lifecycle}


def promote_database_role(path: Path, *, target_role: str = "PRIMARY") -> dict[str, str]:
    """Explicitly promote a validated V2 database; never called implicitly."""
    if target_role != "PRIMARY":
        raise ProductionDatabaseError("DB_ROLE_TARGET_UNSUPPORTED")
    if not Path(path).exists():
        raise ProductionDatabaseError("DB_MISSING")
    with connect(Path(path)) as db:
        metadata = {row[0]: row[1] for row in db.execute("SELECT key,value FROM schema_metadata")}
        if metadata.get("schema_family") != "ACTION_SQLITE_DATA" or metadata.get("schema_version") != "2.0.0":
            raise ProductionDatabaseError("DB_V2_SCHEMA_REQUIRED")
        current = metadata.get("database_role")
        if current == "PRIMARY":
            return {"previous_role": "PRIMARY", "role": "PRIMARY", "status": "ALREADY_PRIMARY"}
        if current != "SHADOW":
            raise ProductionDatabaseError("DB_ROLE_INVALID")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchall():
            raise ProductionDatabaseError("DB_NOT_SAFE_TO_PROMOTE")
        db.execute("UPDATE schema_metadata SET value='PRIMARY' WHERE key='database_role'")
    return {"previous_role": "SHADOW", "role": "PRIMARY", "status": "PROMOTED"}


def mark_export_sync(path: Path, commit_id: str, *, master: Path, known: Path, offline: Path) -> dict[str, Any]:
    """Mark compatibility projections as synchronized after verifying files.

    This never creates or edits the projections.  Generation remains the
    responsibility of the existing Excel/CSV writer; this function records an
    auditable, content-addressed acknowledgement in SQLite.
    """
    files = {"master": Path(master), "known": Path(known), "offline": Path(offline)}
    hashes: dict[str, str | None] = {}
    missing: list[str] = []
    for name, file_path in files.items():
        if not file_path.exists():
            missing.append(name)
            hashes[name] = None
        else:
            hashes[name] = hashlib.sha256(file_path.read_bytes()).hexdigest()
    status = "SUCCESS" if not missing else "PENDING"
    error = None if not missing else "MISSING_PROJECTION:" + ",".join(missing)
    from .connection import connect

    with connect(Path(path)) as db:
        db.execute(
            "UPDATE export_sync SET master_status=?,known_status=?,offline_status=?,master_sha256=?,known_sha256=?,offline_sha256=?,last_attempt_at=CURRENT_TIMESTAMP,error=?,status=? WHERE commit_id=?",
            ("SUCCESS" if "master" not in missing else "PENDING",
             "SUCCESS" if "known" not in missing else "PENDING",
             "SUCCESS" if "offline" not in missing else "PENDING",
             hashes["master"], hashes["known"], hashes["offline"], error, status, commit_id),
        )
    return {"commit_id": commit_id, "status": status, "missing": missing, "hashes": hashes}


def sync_pending_exports(path: Path, *, master: Path, known: Path, offline: Path,
                         commit_id: str | None = None) -> list[dict[str, Any]]:
    """Retry export-sync acknowledgements for pending SQLite commits."""
    if not Path(path).exists():
        raise ProductionDatabaseError("DB_MISSING")
    with connect(Path(path)) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "export_sync" not in tables:
            raise ProductionDatabaseError("DB_V2_SCHEMA_REQUIRED")
        query = "SELECT commit_id FROM export_sync WHERE status != 'SUCCESS'"
        args: tuple[Any, ...] = ()
        if commit_id:
            query += " AND commit_id=?"
            args = (commit_id,)
        ids = [str(row[0]) for row in db.execute(query, args).fetchall()]
    return [mark_export_sync(Path(path), value, master=master, known=known, offline=offline) for value in ids]


def persist_image_manifest(path: Path, manifest_path: Path) -> dict[str, int]:
    """Mirror image manifest metadata into V2 without storing image bytes."""
    from ..images.assets import ImageManifest

    manifest = ImageManifest(Path(manifest_path))
    inserted = 0
    skipped = 0
    with connect(Path(path)) as db:
        try:
            db.execute("SELECT 1 FROM image_assets LIMIT 1")
        except sqlite3.OperationalError as exc:
            raise ProductionDatabaseError("DB_V2_IMAGE_TABLE_MISSING") from exc
        for record in manifest.records.values():
            product = db.execute("SELECT 1 FROM products WHERE official_sku=?", (record.sku,)).fetchone()
            if not product:
                skipped += 1
                continue
            db.execute(
                """INSERT INTO image_assets(official_sku,canonical_id,source_image_url,master_image_path,source_hash,master_hash,width,height,status,first_downloaded_at,last_checked_at,updated_at,error_type)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(official_sku) DO UPDATE SET canonical_id=excluded.canonical_id,source_image_url=excluded.source_image_url,
                   master_image_path=excluded.master_image_path,source_hash=excluded.source_hash,master_hash=excluded.master_hash,
                   width=excluded.width,height=excluded.height,status=excluded.status,first_downloaded_at=COALESCE(image_assets.first_downloaded_at, excluded.first_downloaded_at),
                   last_checked_at=excluded.last_checked_at,updated_at=excluded.updated_at,error_type=excluded.error_type""",
                (record.sku, record.canonical_id, record.source_image_url, record.master_image_path,
                 record.source_hash, record.master_hash, record.master_width or record.source_width,
                 record.master_height or record.source_height,
                 "AVAILABLE" if record.available else record.download_status,
                 record.first_downloaded_at or record.last_downloaded_at, record.last_checked_at,
                 datetime.now(timezone.utc).isoformat(), record.error_type or None),
            )
            inserted += 1
    return {"manifest_records": len(manifest.records), "upserted": inserted, "skipped_unknown_product": skipped}


def validate_production_database(path: Path) -> dict[str, Any]:
    """Run invariant checks without mutating the database."""
    if not Path(path).exists():
        raise ProductionDatabaseError("DB_MISSING")
    with connect(Path(path)) as db:
        metadata = {row[0]: row[1] for row in db.execute("SELECT key,value FROM schema_metadata")}
        if metadata.get("schema_family") != "ACTION_SQLITE_DATA" or metadata.get("schema_version") != "2.0.0":
            raise ProductionDatabaseError("DB_V2_SCHEMA_REQUIRED")
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = db.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok":
            raise ProductionDatabaseError("DB_INTEGRITY_FAILED")
        if foreign_keys:
            raise ProductionDatabaseError("DB_FOREIGN_KEY_FAILED")
        bad_presence = db.execute("SELECT COUNT(*) FROM observations WHERE presence_state NOT IN ('PRESENT','ABSENT','UNKNOWN')").fetchone()[0]
        if bad_presence:
            raise ProductionDatabaseError("DB_PRESENCE_STATE_FAILED")
        return {"integrity": "PASS", "foreign_keys": "PASS", "presence_states": "PASS",
                "products": db.execute("SELECT COUNT(*) FROM products").fetchone()[0],
                "observations": db.execute("SELECT COUNT(*) FROM observations").fetchone()[0],
                "commits": db.execute("SELECT COUNT(*) FROM commit_batches").fetchone()[0]}


def import_legacy_baseline_v2(db_path: Path, *, master_path: Path, state_dir: Path, observed_at: str) -> str:
    """Build a V2 baseline from the legacy read-only Master/State files.

    This is an explicit migration command, not part of daily collection. The
    source files are never modified and the resulting bundle is committed once
    with an auditable BASELINE run id.
    """
    from ..excel.reader import load_current
    from ..state import load_known_skus

    current = load_current(Path(master_path))
    known = load_known_skus(Path(state_dir))
    product_by_sku = {str(r.get("sku") or "").strip(): dict(r) for r in current.values() if str(r.get("sku") or "").strip()}
    for sku, record in known.items():
        product_by_sku.setdefault(sku, {"sku": sku, "canonical_id": record.get("canonical_id"), "status": record.get("last_status", "OFFLINE"), "last_seen": record.get("last_seen_date")})
    products = tuple(product_by_sku.values())
    lifecycle = []
    for sku, record in known.items():
        lifecycle.append({
            "sku": sku,
            "canonical_id": record.get("canonical_id"),
            "first_seen": record.get("first_seen_date"),
            "last_seen": record.get("last_seen_date"),
            "current_status": record.get("last_status", "ACTIVE"),
            "missing_count": record.get("missing_count", 0),
            "last_missing_date": record.get("last_missing_date"),
            "offline_date": record.get("offline_date"),
            "last_state_observation_date": record.get("last_state_observation_date"),
            "ever_offline": record.get("ever_offline", False),
            "last_run_id": record.get("last_run_id"),
        })
    bundle = CommitBundle(
        run_id=f"BASELINE_{observed_at}", observation_date=observed_at, qa_state="PASS",
        current_products=tuple(products), lifecycle_updates=tuple(lifecycle),
        observations=tuple({"run_id": f"BASELINE_{observed_at}", "sku": str(r.get("sku")), "observation_date": observed_at,
                            "presence_state": "PRESENT", "observation_complete": True, "absence_capable": True} for r in products),
        run_record={"dry_run": False, "source": "legacy_baseline"}, snapshot_path=str(master_path),
    )
    return ProductionWriter(Path(db_path)).commit(bundle)
