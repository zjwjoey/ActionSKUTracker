"""Promote one validated daily collection snapshot without collecting again.

The normal production workflow owns collection.  This adapter exists for the
recoverable case where collection and detail extraction completed, but the
formal commit step did not run.  It never revisits the website, translates, or
edits the snapshot.  A snapshot is accepted only when its recorded PRIMARY
head is still current, so an older observation cannot overwrite newer facts.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Mapping

from .. import state as st
from ..database.connection import connect
from ..database.integration import (
    build_daily_bundle,
    commit_daily_bundle,
    database_path,
    regenerate_compatibility_exports,
    storage_mode,
)
from ..database.repository import ProductionRepository
from ..monitor.sku_monitor import SkuStatus
from ..services.runtime import RunLock


_BOOL_FIELDS = {
    "is_new_badge", "promotion", "sustainable", "sitemap_present",
    "listing_present", "nuevo_present", "promotion_present", "observation_valid",
}
_FLOAT_FIELDS = {"current_price", "original_price"}
_INT_FIELDS = {"missing_count"}


def _snapshot_root(paths: Mapping[str, Path], run_id: str) -> Path:
    matches = list(Path(paths["snapshots"]).glob(f"*/{run_id}"))
    if len(matches) != 1:
        raise FileNotFoundError(f"SNAPSHOT_RUN_NOT_FOUND: {run_id}")
    return matches[0]


def _json(path: Path, code: str) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(code)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(code) from exc
    if not isinstance(value, dict):
        raise ValueError(code)
    return value


def _as_bool(value: Any) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes"}


def _as_float(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    return float(text.replace(",", "."))


def _rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        raw_rows = list(csv.DictReader(handle))
    rows: list[dict[str, Any]] = []
    for raw in raw_rows:
        row: dict[str, Any] = {}
        for key, value in raw.items():
            if key in _BOOL_FIELDS:
                row[key] = _as_bool(value)
            elif key in _FLOAT_FIELDS:
                row[key] = _as_float(value)
            elif key in _INT_FIELDS:
                row[key] = int(str(value or "0"))
            else:
                row[key] = value if value not in (None, "") else None
        rows.append(row)
    return rows


def _existing_commit(db_path: Path, run_id: str) -> str | None:
    with connect(db_path) as db:
        row = db.execute(
            "SELECT commit_id FROM commit_batches WHERE run_id=? AND status='COMMITTED'", (run_id,)
        ).fetchone()
    return str(row[0]) if row else None


def _snapshot_payload(root: Path, run_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, dict[str, Any]], dict[str, SkuStatus], set[str], list[dict[str, Any]]]:
    report = _json(root / "run_report.json", "SNAPSHOT_RUN_REPORT_MISSING")
    qa = _json(root / "qa_report.json", "SNAPSHOT_QA_REPORT_MISSING")
    manifest = _json(root / "run_manifest.json", "SNAPSHOT_MANIFEST_MISSING")
    if str(report.get("run_id") or "") != run_id or str(manifest.get("run_id") or "") != run_id:
        raise ValueError("SNAPSHOT_RUN_ID_MISMATCH")
    if str(report.get("qa_state") or "") not in {"PASS", "PASS_PRESENCE_ONLY"} or not bool(qa.get("passed")):
        raise ValueError("SNAPSHOT_QA_NOT_PASS")
    if not bool(report.get("observation_complete")):
        raise ValueError("SNAPSHOT_OBSERVATION_INCOMPLETE")

    delta_rows = _rows(root / "sku_delta.csv")
    presence_rows = _rows(root / "presence_evidence.csv")
    product_rows = _rows(root / "products_normalized.csv")
    if not delta_rows or not presence_rows or not product_rows:
        raise ValueError("SNAPSHOT_REQUIRED_EVIDENCE_MISSING")
    delta = {str(row.get("sku") or ""): row for row in delta_rows if str(row.get("sku") or "")}
    presence = {str(row.get("sku") or ""): row for row in presence_rows if str(row.get("sku") or "")}
    products = {str(row.get("sku") or ""): row for row in product_rows if str(row.get("sku") or "")}
    if set(delta) != set(presence):
        raise ValueError("SNAPSHOT_PRESENCE_DELTA_MISMATCH")

    today_set = {
        sku for sku, row in presence.items()
        if any(bool(row.get(key)) for key in ("sitemap_present", "listing_present", "nuevo_present", "promotion_present"))
    }
    if set(products) != today_set:
        raise ValueError("SNAPSHOT_CURRENT_PRODUCT_SET_MISMATCH")
    return report, qa, manifest, products, {}, today_set, delta_rows


def _statuses(delta_rows: list[dict[str, Any]], root: Path, baseline: Mapping[str, Mapping[str, Any]], known: Mapping[str, Mapping[str, Any]]) -> tuple[dict[str, SkuStatus], set[str]]:
    presence = {str(row.get("sku") or ""): row for row in _rows(root / "presence_evidence.csv") if str(row.get("sku") or "")}
    result: dict[str, SkuStatus] = {}
    today_set: set[str] = set()
    for row in delta_rows:
        sku = str(row.get("sku") or "")
        if not sku:
            continue
        evidence = presence[sku]
        present = any(bool(evidence.get(key)) for key in ("sitemap_present", "listing_present", "nuevo_present", "promotion_present"))
        if present:
            today_set.add(sku)
        previous = dict(known.get(sku) or {})
        result[sku] = SkuStatus(
            sku=sku,
            canonical_id=str(row.get("canonical_id") or f"ACT{sku.zfill(7)}"),
            status=str(row.get("status") or "UNKNOWN"),
            source_flag=str(row.get("source_flag") or "NONE"),
            sitemap_present=bool(evidence.get("sitemap_present")),
            listing_present=bool(evidence.get("listing_present")),
            was_yesterday=sku in baseline,
            ever_seen=sku in known,
            first_seen=previous.get("first_seen_date") or None,
            previous_status=str(previous.get("last_status") or ""),
            missing_count=int(row.get("missing_count") or 0),
            event=str(row.get("event") or "") or None,
            observation_valid=bool(evidence.get("observation_valid")),
            nuevo_present=bool(evidence.get("nuevo_present")),
            promotion_present=bool(evidence.get("promotion_present")),
        )
    return result, today_set


def ingest_snapshot(cfg: dict[str, Any], run_id: str, *, commit: bool = False) -> dict[str, Any]:
    """Preflight or commit a frozen daily snapshot into SQLite PRIMARY.

    The preflight is read-only.  ``commit=True`` is the explicit production
    action and uses the regular CommitBundle, collection-quality, transaction,
    and compatibility-projection paths.
    """
    if storage_mode(cfg) != "SQLITE_PRIMARY":
        raise ValueError("SNAPSHOT_INGEST_REQUIRES_SQLITE_PRIMARY")
    root = _snapshot_root(cfg["paths"], run_id)
    db_path = database_path(cfg)
    existing = _existing_commit(db_path, run_id)
    if existing:
        return {"run_id": run_id, "status": "ALREADY_COMMITTED", "commit_id": existing, "snapshot": str(root)}

    report, _qa, manifest, products, _unused, expected_today, delta_rows = _snapshot_payload(root, run_id)
    base_commit_id = str(manifest.get("source_commit_id") or report.get("source_commit_id") or "")
    if not base_commit_id:
        raise ValueError("SNAPSHOT_BASE_COMMIT_MISSING")
    repo = ProductionRepository(db_path)
    current_head = repo.current_head()
    if current_head != base_commit_id:
        raise ValueError("BASELINE_CHANGED_BEFORE_SNAPSHOT_INGEST")

    statuses, today_set = _statuses(delta_rows, root, repo.load_current_products(), repo.load_known_skus())
    if today_set != expected_today:
        raise ValueError("SNAPSHOT_PRESENCE_PRODUCT_SET_MISMATCH")
    review_marker = root / "review_rows.csv"
    if not review_marker.exists():
        raise ValueError("SNAPSHOT_REVIEW_EVIDENCE_MISSING")
    preview = {
        "run_id": run_id,
        "status": "READY_TO_INGEST",
        "snapshot": str(root),
        "base_commit_id": base_commit_id,
        "current_sku": len(products),
        "observation_count": len(statuses),
        "details_completed": int(report.get("detail_completed") or 0),
        "details_incomplete": int(report.get("detail_incomplete") or 0),
    }
    if not commit:
        return preview

    lock = RunLock(cfg["paths"]["state"], stale_minutes=cfg["run"].get("lock_stale_minutes", 180))
    lock.acquire(f"snapshot-ingest_{run_id}", command=f"snapshot-ingest --run-id {run_id} --commit")
    try:
        # Recheck under the write lock, then persist fresh collection-quality
        # evidence for the immutable snapshot before the normal DB transaction.
        repo = ProductionRepository(db_path)
        if repo.current_head() != base_commit_id:
            raise ValueError("BASELINE_CHANGED_BEFORE_SNAPSHOT_INGEST")
        baseline = repo.load_current_products()
        known = repo.load_known_skus()
        statuses, today_set = _statuses(delta_rows, root, baseline, known)
        transition = st.apply_state_transition(
            known, statuses, str(report.get("run_date") or ""), run_id,
            int(cfg["lifecycle"]["offline_confirmation_runs"]),
        )
        run_record = {**report, "dry_run": False, "commit_status": "SNAPSHOT_INGEST_PENDING",
                      "ingested_from_snapshot": run_id, "snapshot_original_dry_run": bool(report.get("dry_run"))}
        from .daily import _evaluate_daily_collection_quality, _should_commit
        _evaluate_daily_collection_quality(cfg, run_id, run_record, dry_run=False)
        if not _should_commit(
            dry_run=False,
            qa_passed=True,
            access_state=str(report.get("presence_access_state") or "NORMAL"),
            qa_state=str(report.get("qa_state") or ""),
            collection_quality_state=run_record.get("collection_quality_state"),
            collection_quality_override=bool(run_record.get("collection_quality_override", False)),
            collection_quality_override_evidence=run_record.get("collection_quality_override_evidence"),
            workflow_v2_shadow_state=(run_record.get("workflow_v2_shadow") or {}).get("status"),
            requires_collection_integrity=True,
        ):
            raise ValueError("SNAPSHOT_INGEST_GATE_BLOCKED")
        staging = Path(cfg["paths"]["staging"]) / run_id
        bundle = build_daily_bundle(
            run_id=run_id,
            observation_date=str(report.get("run_date") or ""),
            qa_state=str(report.get("qa_state") or ""),
            today_records=products,
            baseline=baseline,
            statuses=statuses,
            known=known,
            transition=transition,
            today_set=today_set,
            observation_complete=True,
            price_events=_rows(staging / "price_changes.csv"),
            event_events=_rows(staging / "event_changes.csv"),
            review_rows=_rows(review_marker),
            run_record=run_record,
            snapshot_path=root,
        )
        commit_id = commit_daily_bundle(cfg, bundle, mode="SQLITE_PRIMARY")
        projection = regenerate_compatibility_exports(cfg, commit_id)
        return {**preview, "status": "COMMITTED", "commit_id": commit_id, "compatibility_projection": projection}
    finally:
        lock.release()
