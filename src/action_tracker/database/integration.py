"""Bridge the daily monitor result into the SQLite V2 commit contract.

The collection/lifecycle code remains the owner of business decisions.  This
module only translates the already computed result into a :class:`CommitBundle`
and provides the storage-mode switch used by the orchestrator.  Keeping this
translation in one place prevents the Excel and SQLite paths from silently
drifting apart.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from .production import CommitBundle, ProductionWriter, ProductionDatabaseError, mark_export_sync
from .connection import connect
from ..knowledge.contracts import source_hash as knowledge_source_hash
from ..localization.formatter import format_unit_price


VALID_STORAGE_MODES = {"EXCEL_PRIMARY", "SQLITE_SHADOW", "SQLITE_PRIMARY"}


def storage_mode(cfg: Mapping[str, Any]) -> str:
    mode = str((cfg.get("storage") or {}).get("mode") or "EXCEL_PRIMARY").strip().upper()
    if mode not in VALID_STORAGE_MODES:
        raise ValueError(f"STORAGE_MODE_INVALID: {mode}")
    return mode


def database_path(cfg: Mapping[str, Any]) -> Path:
    raw = (cfg.get("storage") or {}).get("db_path")
    path = Path(raw or (Path(cfg["project_root"]) / "runtime" / "db" / "action_tracker.db"))
    if not path.is_absolute():
        path = Path(cfg["project_root"]) / path
    return path


def latest_commit_id(path: Path) -> str | None:
    """Return the current SQLite commit head without changing the database."""
    if not path.exists():
        return None
    from .connection import connect

    with connect(path) as db:
        try:
            row = db.execute(
                "SELECT commit_id FROM commit_batches "
                "WHERE status='COMMITTED' ORDER BY committed_at DESC LIMIT 1"
            ).fetchone()
        except Exception:
            return None
    return str(row[0]) if row else None


def snapshot_digest(directory: Path | None) -> str | None:
    """Hash all files in a snapshot in stable path order for audit evidence."""
    if directory is None or not directory.exists():
        return None
    digest = hashlib.sha256()
    files = sorted(path for path in directory.rglob("*") if path.is_file())
    for path in files:
        digest.update(path.relative_to(directory).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def build_daily_bundle(
    *,
    run_id: str,
    observation_date: str,
    qa_state: str,
    today_records: Mapping[str, Mapping[str, Any]],
    baseline: Mapping[str, Mapping[str, Any]],
    statuses: Mapping[str, Any],
    known: Mapping[str, Mapping[str, Any]],
    transition: Mapping[str, Any],
    today_set: set[str],
    observation_complete: bool,
    price_events: list[dict[str, Any]],
    event_events: list[dict[str, Any]],
    review_rows: list[dict[str, Any]],
    run_record: Mapping[str, Any],
    snapshot_path: Path | None = None,
) -> CommitBundle:
    """Create one immutable SQLite payload from a completed daily calculation.

    Historical identities are included as minimal product rows so observations
    and lifecycle state can reference them.  Only today's Presence set is
    marked ``CURRENT``; this mirrors the existing CURRENT-sheet invariant.
    """
    product_by_sku: dict[str, dict[str, Any]] = {
        str(sku): dict(record) for sku, record in today_records.items()
    }
    transition_known = transition.get("known") or {}
    for sku, status in statuses.items():
        sku = str(sku)
        if sku in product_by_sku:
            continue
        source = dict(baseline.get(sku) or known.get(sku) or {})
        source.setdefault("sku", sku)
        source.setdefault("canonical_id", getattr(status, "canonical_id", f"ACT{sku.zfill(7)}"))
        # The lifecycle transition is authoritative for historical identities.
        # A post-OFFLINE absence is classified as ABSENT by the observation
        # state machine (meaning "no new transition"), but the product
        # projection must remain OFFLINE so CURRENT/export and operations
        # health do not diverge from lifecycle_state.
        lifecycle_row = transition_known.get(sku) or {}
        source["status"] = _product_status(
            lifecycle_row.get("current_status")
            or getattr(status, "status", None),
            source.get("status"),
        )
        source["_historical_minimal"] = True
        product_by_sku[sku] = source
    # A known identity can be absent from the current status map in older
    # state-file versions; retain it as a minimal historical product too.
    for sku, record in known.items():
        sku = str(sku)
        if sku not in product_by_sku:
            source = dict(baseline.get(sku) or record or {})
            source.setdefault("sku", sku)
            source.setdefault("canonical_id", record.get("canonical_id") or f"ACT{sku.zfill(7)}")
            source["status"] = _product_status(record.get("last_status"), record.get("last_status"))
            source["_historical_minimal"] = True
            product_by_sku[sku] = source

    products = tuple(product_by_sku.values())
    localizations: list[dict[str, Any]] = []
    for record in products:
        sku = str(record.get("sku") or "")
        if not sku or record.get("_historical_minimal"):
            continue
        localizations.append(_localization(record, "es"))
        localizations.append(_localization(record, "zh"))

    lifecycle: list[dict[str, Any]] = []
    for sku, record in transition_known.items():
        row = dict(record)
        row.setdefault("sku", sku)
        row.setdefault("canonical_id", record.get("canonical_id") or f"ACT{str(sku).zfill(7)}")
        # ``apply_state_transition`` already updates last_run_id only for
        # identities that participated in this observation.  Do not stamp
        # untouched historical/offline rows with the current run: doing so
        # makes the SQLite lifecycle projection diverge from known_skus.csv.
        lifecycle.append(row)
    # If there is no transition entry (e.g. a fixture or a future partial
    # writer), seed a safe lifecycle row from the status itself.
    for sku, status in statuses.items():
        if any(str(row.get("sku") or row.get("official_sku")) == str(sku) for row in lifecycle):
            continue
        lifecycle.append({
            "sku": str(sku),
            "canonical_id": getattr(status, "canonical_id", f"ACT{str(sku).zfill(7)}"),
            "first_seen": getattr(status, "first_seen", None) or observation_date,
            "last_seen": observation_date if str(sku) in today_set else None,
            "current_status": _product_status(getattr(status, "status", None), "ACTIVE"),
            "missing_count": getattr(status, "missing_count", 0),
            "last_state_observation_date": observation_date,
            "ever_offline": str(getattr(status, "status", "")) == "OFFLINE",
            "last_run_id": run_id,
        })

    observations = []
    for sku, status in statuses.items():
        present = str(sku) in today_set
        valid = bool(getattr(status, "observation_valid", False))
        state = "PRESENT" if present else ("ABSENT" if valid else "UNKNOWN")
        observations.append({
            "run_id": run_id,
            "sku": str(sku),
            "observation_date": observation_date,
            "presence_state": state,
            "sitemap_present": int(bool(getattr(status, "sitemap_present", False))),
            "listing_present": int(bool(getattr(status, "listing_present", False))),
            "nuevo_present": int(bool(getattr(status, "nuevo_present", False))),
            "promotion_present": int(bool(getattr(status, "promotion_present", False))),
            "observation_complete": bool(observation_complete and valid),
            "absence_capable": bool(valid and not present or observation_complete),
            "current_price": (today_records.get(str(sku)) or {}).get("current_price"),
            "source_flag": getattr(status, "source_flag", None),
        })

    prices = tuple(_price_event(row, run_id) for row in price_events)
    events = tuple(_event(row, run_id) for row in event_events)
    report = dict(run_record)
    report.setdefault("run_id", run_id)
    report.setdefault("run_date", observation_date)
    report.setdefault("qa_state", qa_state)
    report.setdefault("dry_run", False)
    return CommitBundle(
        run_id=run_id,
        observation_date=observation_date,
        qa_state=qa_state,
        current_products=products,
        localization_updates=tuple(localizations),
        lifecycle_updates=tuple(lifecycle),
        observations=tuple(observations),
        price_events=prices,
        event_events=events,
        review_rows=tuple(review_rows),
        run_record=report,
        snapshot_path=str(snapshot_path) if snapshot_path else None,
        snapshot_hash=snapshot_digest(snapshot_path),
    )


def commit_daily_bundle(cfg: Mapping[str, Any], bundle: CommitBundle, *, mode: str | None = None) -> str:
    """Commit a bundle with the configured database role and baseline gate."""
    resolved_mode = mode or storage_mode(cfg)
    role = "PRIMARY" if resolved_mode == "SQLITE_PRIMARY" else "SHADOW"
    path = database_path(cfg)
    threshold = float((cfg.get("qa") or {}).get("max_localization_coverage_drop", 0.5))
    writer = ProductionWriter(path, role=role, localization_drop_threshold=threshold)
    if bundle.base_commit_id is None:
        # The writer performs the actual optimistic check.  This branch merely
        # makes the intent explicit and keeps first-commit behavior deterministic.
        base = latest_commit_id(path)
        if base is not None:
            bundle = _with_base_commit(bundle, base)
    return writer.commit(bundle)


def acknowledge_compatibility_exports(cfg: Mapping[str, Any], commit_id: str) -> dict[str, Any]:
    """Record that the legacy Excel/CSV projections are present and hashed."""
    state = Path(cfg["paths"]["state"])
    return mark_export_sync(
        database_path(cfg), commit_id,
        master=Path(cfg["paths"]["master"]),
        known=state / "known_skus.csv",
        offline=state / "offline_skus.csv",
    )


def regenerate_compatibility_exports(cfg: Mapping[str, Any], commit_id: str) -> dict[str, Any]:
    """Rebuild Master/State projections from the current SQLite PRIMARY head.

    All files are staged and validated before publication.  If a later
    replacement fails, the previous bytes are restored so a committed DB is
    never paired with a half-written compatibility view.
    """
    if storage_mode(cfg) != "SQLITE_PRIMARY":
        raise ProductionDatabaseError("EXPORT_SYNC_REQUIRES_SQLITE_PRIMARY")
    from .repository import ProductionRepository
    from .. import state as st
    from ..excel.writer import commit_master, restore_master_from_backup, stage_master

    db_path = database_path(cfg)
    repo = ProductionRepository(db_path)
    if repo.current_head() != commit_id:
        raise ProductionDatabaseError("EXPORT_SYNC_COMMIT_NOT_CURRENT_HEAD")
    records = repo.load_current_export_records()
    known = repo.load_known_skus()
    offline = repo.load_offline_skus()
    master = Path(cfg["paths"]["master"])
    state_dir = Path(cfg["paths"]["state"])
    known_path = state_dir / "known_skus.csv"
    offline_path = state_dir / "offline_skus.csv"
    run_log_revisions = _run_log_revisions(db_path, commit_id)
    master_tmp, master_backup = stage_master(
        dict(cfg), updated_records={str(r["sku"]): r for r in records},
        price_events=[], event_events=[], return_backup=True,
        compatibility_projection=True, run_log_revisions=run_log_revisions,
    )
    known_tmp, _ = st.stage_known_skus(state_dir, known)
    offline_tmp, _ = st.stage_offline_skus(state_dir, offline)
    previous = {p: (p.read_bytes() if p.exists() else None) for p in (known_path, offline_path)}
    try:
        commit_master(master_tmp, master, compatibility_projection=True)
        st.commit_state_file(known_tmp, known_path)
        st.commit_state_file(offline_tmp, offline_path)
    except Exception:
        if master_backup.exists():
            restore_master_from_backup(master_backup, master)
        for path, payload in previous.items():
            if payload is None:
                path.unlink(missing_ok=True)
            else:
                restore_tmp = path.with_name(path.name + ".restore")
                restore_tmp.write_bytes(payload)
                os.replace(restore_tmp, path)
        raise
    result = mark_export_sync(db_path, commit_id, master=master, known=known_path, offline=offline_path)
    result["rebuild"] = {"current": len(records), "known": len(known), "offline": len(offline),
                         "run_log_revisions": len(run_log_revisions)}
    return result


def _run_log_revisions(db_path: Path, commit_id: str) -> dict[str, dict[str, Any]]:
    """Project corrected derived event counts back into an existing run log."""
    with connect(db_path) as db:
        row = db.execute(
            """SELECT c.run_id,e.evidence_json FROM commit_batches c
               JOIN run_evidence e ON e.run_id=c.run_id WHERE c.commit_id=?""",
            (commit_id,),
        ).fetchone()
        if row is None:
            return {}
        try:
            stored = json.loads(row[1] or "{}")
        except json.JSONDecodeError:
            return {}
        original = stored.get("run_log")
        if not isinstance(original, dict):
            return {}
        run_id = str(row[0])
        revised = dict(original)
        event_counts = {
            str(event_type): int(count)
            for event_type, count in db.execute(
                "SELECT event_type,COUNT(*) FROM event_history WHERE run_id=? GROUP BY event_type", (run_id,)
            )
        }
        revised.update({
            "PROMO_START": event_counts.get("PROMO_START", 0),
            "PROMO_END": event_counts.get("PROMO_END", 0),
            "NEW_BADGE_ON": event_counts.get("ACTION_NEW_BADGE_ON", 0),
            "NEW_BADGE_OFF": event_counts.get("ACTION_NEW_BADGE_OFF", 0),
            "CONTENT_CHANGE": event_counts.get("CONTENT_CHANGE", 0),
            "PRICE_UP": int(db.execute("SELECT COUNT(*) FROM price_history WHERE run_id=? AND change_type='UP'", (run_id,)).fetchone()[0]),
            "PRICE_DOWN": int(db.execute("SELECT COUNT(*) FROM price_history WHERE run_id=? AND change_type='DOWN'", (run_id,)).fetchone()[0]),
        })
    return {run_id: revised}


def regenerate_pending_exports(cfg: Mapping[str, Any], *, commit_id: str | None = None) -> list[dict[str, Any]]:
    """Rebuild every pending compatibility projection, or one requested commit."""
    db_path = database_path(cfg)
    with connect(db_path) as db:
        if commit_id:
            row = db.execute("SELECT status FROM export_sync WHERE commit_id=?", (commit_id,)).fetchone()
            if row and str(row[0]) == "SUPERSEDED":
                raise ProductionDatabaseError("EXPORT_SYNC_COMMIT_SUPERSEDED")
        query = "SELECT commit_id FROM export_sync WHERE status IN ('PENDING','FAILED')"
        args: tuple[Any, ...] = ()
        if commit_id:
            query += " AND commit_id=?"
            args = (commit_id,)
        ids = [str(row[0]) for row in db.execute(query, args).fetchall()]
    results: list[dict[str, Any]] = []
    for value in ids:
        try:
            results.append(regenerate_compatibility_exports(cfg, value))
        except Exception as exc:
            # Keep the sync record operationally visible and retryable.  A
            # later formal commit will supersede it; otherwise a resume can
            # retry this exact current-head projection without recollecting.
            with connect(db_path) as db:
                db.execute(
                    "UPDATE export_sync SET status='FAILED',error=?,last_attempt_at=CURRENT_TIMESTAMP WHERE commit_id=? AND status IN ('PENDING','FAILED')",
                    (f"{type(exc).__name__}: {exc}", value),
                )
            results.append({"commit_id": value, "status": "FAILED", "error": f"{type(exc).__name__}: {exc}"})
    return results


def _with_base_commit(bundle: CommitBundle, base: str) -> CommitBundle:
    return CommitBundle(
        **{**bundle.__dict__, "base_commit_id": base, "bundle_hash": None}
    )


def _product_status(value: Any, fallback: Any) -> str:
    text = str(value or fallback or "ACTIVE").strip().upper()
    return {"NEW": "ACTIVE", "REAPPEARED": "ACTIVE", "MISSING_FIRST": "MISSING",
            "MISSING_CONTINUED": "MISSING"}.get(text, text or "ACTIVE")


def _localization(record: Mapping[str, Any], language: str) -> dict[str, Any]:
    digest = knowledge_source_hash(record)
    if language == "es":
        return {"sku": record.get("sku"), "language": "es", "name": record.get("name_es"),
                "cat1": record.get("cat1_es"), "cat2": record.get("cat2_es"),
                "spec": record.get("spec_es"), "unit_price": record.get("unit_price"), "description": record.get("desc_es"),
                "details": record.get("details_es"), "source": "OFFICIAL_FACT",
                "review_status": "VERIFIED", "source_hash": digest,
                "resolution_status": "APPLIED", "freshness_status": "CURRENT",
                "name_source": "official_fact", "cat1_source": "official_fact",
                "cat2_source": "official_fact", "spec_source": "official_fact", "unit_price_source": "official_unit_price",
                "description_source": "official_fact", "details_source": "official_fact"}
    return {"sku": record.get("sku"), "language": "zh", "name": record.get("name_zh"),
            "cat1": record.get("cat1_zh"), "cat2": record.get("cat2_zh"),
            "spec": record.get("spec_zh"), "unit_price": format_unit_price(str(record.get("unit_price") or "")), "description": record.get("desc_zh"),
            "details": record.get("details_zh"), "source": "DICTIONARY_OR_FALLBACK",
            "review_status": record.get("translation_status") or "PENDING",
            "source_hash": digest, "resolution_status": record.get("translation_status") or "PENDING",
            "freshness_status": "CURRENT", "name_source": "dictionary_or_fallback",
            "cat1_source": "dictionary_or_fallback", "cat2_source": "dictionary_or_fallback",
            "spec_source": "dictionary_or_fallback", "unit_price_source": "official_unit_price", "description_source": "dictionary_or_fallback",
            "details_source": "dictionary_or_fallback"}


def _price_event(row: Mapping[str, Any], run_id: str) -> dict[str, Any]:
    return {"canonical_id": row.get("Canonical_ID") or row.get("canonical_id"),
            "sku": row.get("SKU") or row.get("sku"),
            "date": row.get("日期") or row.get("observed_at"),
            "old_price": row.get("旧售价 (€)") if "旧售价 (€)" in row else row.get("old_price"),
            "new_price": row.get("新售价 (€)") if "新售价 (€)" in row else row.get("new_price"),
            "change_type": row.get("变化类型") or row.get("change_type"), "run_id": run_id}


def _event(row: Mapping[str, Any], run_id: str) -> dict[str, Any]:
    return {"canonical_id": row.get("Canonical_ID") or row.get("canonical_id"),
            "sku": row.get("SKU") or row.get("sku"),
            "date": row.get("日期") or row.get("occurred_at"),
            "event_type": row.get("事件类型") or row.get("event_type"),
            "old_value": row.get("旧值") if "旧值" in row else row.get("old_value"),
            "new_value": row.get("新值") if "新值" in row else row.get("new_value"),
            "evidence": row.get("备注") or row.get("evidence"), "run_id": run_id}
