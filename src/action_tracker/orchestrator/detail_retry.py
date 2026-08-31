"""Retry unfinished detail enrichment from an existing observation snapshot.

This is deliberately not an observation: it never scans listings, advances
lifecycle state, or writes Master/state files.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..excel import reader, writer
from ..products import updater
from ..services.access import AccessController
from ..services.browser import BrowserSession
from ..services.runtime import RunLock, madrid_now


_DETAIL_REASONS = {"NEW", "REAPPEARED", "MISSING_FIELD", "DETAIL_REFRESH"}

# These are factual fields obtained from a product-detail page.  An apply must
# never use a retry to alter availability, pricing, badges, or lifecycle data:
# those continue to come only from the committed listing observation.
_DETAIL_MASTER_FIELDS = (
    "name_es",
    "cat1_es",
    "cat2_es",
    "spec_es",
    "desc_es",
    "details_es",
    "product_url",
    "image_url",
)


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _snapshot_root(paths: dict[str, Path], run_id: str) -> Path:
    matches = list(paths["snapshots"].glob(f"*/{run_id}"))
    if len(matches) != 1:
        raise FileNotFoundError(f"PARENT_RUN_NOT_FOUND: {run_id}")
    return matches[0]


def _completed_skus(parent: Path) -> set[str]:
    completed = set(updater._read_ckpt(parent / "detail_fetch.jsonl"))
    for retry_file in parent.glob("detail_retries/*/detail_fetch.jsonl"):
        completed.update(updater._read_ckpt(retry_file))
    return completed


def _completed_details(parent: Path) -> dict[str, dict]:
    """Read detail checkpoints in execution order, keeping the latest per SKU."""
    checkpoint_files = [parent / "detail_fetch.jsonl"]
    checkpoint_files.extend(sorted(parent.glob("detail_retries/*/detail_fetch.jsonl")))
    completed: dict[str, dict] = {}
    for checkpoint in checkpoint_files:
        for sku, entry in updater._read_ckpt(checkpoint).items():
            detail = entry.get("detail") if isinstance(entry, dict) else None
            if not isinstance(detail, dict):
                raise ValueError(f"INVALID_DETAIL_CHECKPOINT: {checkpoint} sku={sku}")
            if str(detail.get("sku") or sku) != str(sku):
                raise ValueError(f"DETAIL_SKU_MISMATCH: {checkpoint} sku={sku}")
            completed[str(sku)] = detail
    return completed


def _plans(parent: Path) -> list[dict]:
    updates = _rows(parent / "product_updates.csv")
    if not updates:
        raise ValueError("PARENT_DETAIL_CANDIDATES_MISSING: product_updates.csv")
    products = {row.get("sku"): row for row in _rows(parent / "products_normalized.csv")}
    listings = {row.get("sku"): row for row in _rows(parent / "listing_products.csv")}
    out = []
    for row in updates:
        needs = str(row.get("need_detail", "")).lower() in {"1", "true", "yes"}
        if "need_detail" not in row:
            needs = row.get("reason") in _DETAIL_REASONS
        if not needs:
            continue
        sku = str(row.get("sku") or "")
        light = dict(listings.get(sku) or {})
        light.update({k: v for k, v in (products.get(sku) or {}).items()
                      if k in {"product_url", "current_price", "original_price", "unit_price", "discount", "raw_tags", "image_url", "spec_es", "name_es", "cat1_es"} and v not in (None, "")})
        out.append({"sku": sku, "canonical_id": row.get("canonical_id") or "", "reason": row.get("reason") or "DETAIL_REFRESH",
                    "need_detail": True, "light": light})
    return out


def run_detail_retry(cfg: dict[str, Any], parent_run_id: str) -> dict:
    paths: dict[str, Path] = cfg["paths"]
    parent = _snapshot_root(paths, parent_run_id)
    parent_report = json.loads((parent / "run_report.json").read_text(encoding="utf-8"))
    all_plans = _plans(parent)
    completed_before = _completed_skus(parent)
    pending = [plan for plan in all_plans if plan["sku"] not in completed_before]
    started = madrid_now()
    retry_id = f"detail-retry_{started.strftime('%Y%m%d_%H%M%S')}"
    retry_dir = parent / "detail_retries" / retry_id
    retry_dir.mkdir(parents=True, exist_ok=False)
    lock = RunLock(paths["state"], stale_minutes=cfg["run"].get("lock_stale_minutes", 180))
    lock.acquire(retry_id, command=f"detail-retry --run-id {parent_run_id}")
    access = AccessController(cooldown_seconds=cfg["browser"].get("cooldown_seconds", 60),
                              degraded_recovery_successes=cfg["browser"].get("degraded_recovery_successes", 3))
    evidence: list[dict] = []
    completed: list[str] = []
    error: BaseException | None = None
    try:
        baseline = {row.get("sku"): row for row in _rows(parent / "products_normalized.csv")}
        with BrowserSession(cfg["browser"], cfg["browser"].get("cookies_path"), access_controller=access) as browser:
            updater.fetch_and_merge(browser, pending, baseline, retry_dir,
                                    cfg["lifecycle"]["max_detail_retries"], access_controller=access,
                                    detail_evidence=evidence, detail_completed_skus=completed,
                                    evidence_context={"parent_run_id": parent_run_id, "retry_id": retry_id,
                                                      "observation_date": parent_report.get("run_date"), **browser.manifest()})
    except BaseException as exc:
        error = exc
        raise
    finally:
        report = {"retry_id": retry_id, "parent_run_id": parent_run_id,
                  "observation_date": parent_report.get("run_date"), "started_at": started.isoformat(),
                  "finished_at": madrid_now().isoformat(), "pending_before": len(pending), "planned": len(pending),
                  "completed": len(completed), "failed": len(evidence),
                  "pending_after": max(0, len(pending) - len(completed)), "final_access_state": access.state.value,
                  "detail_retry_pass": bool(pending) and len(completed) == len(pending) and access.state.value == "NORMAL",
                  "fatal_error": None if error is None else {"type": type(error).__name__, "message": str(error)},
                  "lock_released": False}
        try:
            (retry_dir / "detail_evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
            (retry_dir / "detail_retry_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        finally:
            lock.release()
            report["lock_released"] = True
            (retry_dir / "detail_retry_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def apply_detail_retry(cfg: dict[str, Any], parent_run_id: str) -> dict:
    """Merge a complete, validated detail retry into an already committed Master.

    This is intentionally a separate, explicit action.  It accepts only a
    formally committed and QA-passing listing observation, and updates only
    fields which are authoritative on product detail pages.
    """
    paths: dict[str, Path] = cfg["paths"]
    parent = _snapshot_root(paths, parent_run_id)
    report_path = parent / "run_report.json"
    if not report_path.exists():
        raise ValueError("PARENT_RUN_REPORT_MISSING")
    parent_report = json.loads(report_path.read_text(encoding="utf-8"))
    if parent_report.get("qa_state") != "PASS" or not parent_report.get("observation_complete"):
        raise ValueError("PARENT_OBSERVATION_NOT_QA_PASS")
    if parent_report.get("dry_run") or parent_report.get("commit_status") != "FULL_COMMIT":
        raise ValueError("PARENT_OBSERVATION_NOT_COMMITTED")

    planned = {plan["sku"] for plan in _plans(parent)}
    if not planned:
        raise ValueError("PARENT_DETAIL_CANDIDATES_MISSING")
    completed = _completed_details(parent)
    unexpected = set(completed) - planned
    if unexpected:
        raise ValueError(f"DETAIL_CHECKPOINT_NOT_PLANNED: {sorted(unexpected)[:5]}")
    pending = planned - set(completed)
    if pending:
        raise ValueError(f"DETAIL_RETRY_INCOMPLETE: {len(pending)} SKU(s) still pending")

    from ..database.integration import database_path, regenerate_compatibility_exports, storage_mode
    if storage_mode(cfg) == "SQLITE_PRIMARY":
        from ..database.production import apply_detail_corrections
        result = apply_detail_corrections(
            database_path(cfg), parent_run_id=parent_run_id, details_by_sku=completed, mode="APPLY",
            source_run_date=str(parent_report.get("run_date") or ""),
        )
        # Excel/CSV are compatibility projections only.  Rebuild them from
        # the corrected PRIMARY facts; never route Detail through the legacy
        # Master writer in this mode.
        result["compatibility_projection"] = regenerate_compatibility_exports(cfg, result["commit_id"])
        return result

    current = reader.load_current(paths["master"])
    expected_current = parent_report.get("today_sku")
    if expected_current is not None and len(current) != int(expected_current):
        raise ValueError(
            f"MASTER_CURRENT_COUNT_CHANGED: expected={expected_current} actual={len(current)}"
        )
    absent = planned - set(current)
    if absent:
        raise ValueError(f"DETAIL_SKU_NOT_CURRENT: {sorted(absent)[:5]}")

    changed_skus = 0
    changed_fields = 0
    for sku in sorted(planned):
        record = current[sku]
        detail = completed[sku]
        changed = False
        for field in _DETAIL_MASTER_FIELDS:
            value = detail.get(field)
            if value in (None, "") or record.get(field) == value:
                continue
            record[field] = value
            changed = True
            changed_fields += 1
        if changed:
            changed_skus += 1

    if changed_skus:
        writer.write_master(
            cfg,
            updated_records=current,
            price_events=[],
            event_events=[],
            run_log_row=None,
            review_rows=None,
            dry_run=False,
        )
    return {
        "parent_run_id": parent_run_id,
        "planned": len(planned),
        "completed": len(completed),
        "applied_skus": changed_skus,
        "applied_fields": changed_fields,
        "master": str(paths["master"]),
    }


def backfill_missing_details(cfg: dict[str, Any], source_run_id: str) -> dict:
    """Fill only blank current detail fields from a validated historical snapshot.

    This supports recovery after a QA-passing dry-run obtained the details but
    could not formally commit its listing observation.  It is deliberately
    narrower than ``apply_detail_retry``: historical evidence can only fill
    blanks on SKUs that are still CURRENT, and can never overwrite newer facts.
    """
    paths: dict[str, Path] = cfg["paths"]
    source = _snapshot_root(paths, source_run_id)
    report_path = source / "run_report.json"
    if not report_path.exists():
        raise ValueError("SOURCE_RUN_REPORT_MISSING")
    source_report = json.loads(report_path.read_text(encoding="utf-8"))
    if source_report.get("qa_state") != "PASS" or not source_report.get("observation_complete"):
        raise ValueError("SOURCE_OBSERVATION_NOT_QA_PASS")

    planned = {plan["sku"] for plan in _plans(source)}
    completed = _completed_details(source)
    if not planned or planned - set(completed):
        raise ValueError("SOURCE_DETAIL_EVIDENCE_INCOMPLETE")

    from ..database.integration import database_path, regenerate_compatibility_exports, storage_mode
    if storage_mode(cfg) == "SQLITE_PRIMARY":
        from ..database.production import apply_detail_corrections
        result = apply_detail_corrections(
            database_path(cfg), parent_run_id=source_run_id, details_by_sku=completed, mode="BACKFILL",
            source_run_date=str(source_report.get("run_date") or ""),
        )
        result["compatibility_projection"] = regenerate_compatibility_exports(cfg, result["commit_id"])
        return result

    current = reader.load_current(paths["master"])
    candidates = planned & set(current)
    changed_skus = 0
    changed_fields = 0
    for sku in sorted(candidates):
        record = current[sku]
        detail = completed[sku]
        # A historical record must contain actual detail content before it can
        # be used as a source for the recovery path.
        if not detail.get("desc_es") or not detail.get("details_es"):
            raise ValueError(f"SOURCE_DETAIL_CONTENT_MISSING: {sku}")
        changed = False
        for field in _DETAIL_MASTER_FIELDS:
            value = detail.get(field)
            if value in (None, "") or record.get(field) not in (None, ""):
                continue
            record[field] = value
            changed = True
            changed_fields += 1
        if changed:
            changed_skus += 1

    if changed_skus:
        writer.write_master(
            cfg,
            updated_records=current,
            price_events=[],
            event_events=[],
            run_log_row=None,
            review_rows=None,
            dry_run=False,
        )
    return {
        "source_run_id": source_run_id,
        "source_detail_evidence": len(completed),
        "still_current_candidates": len(candidates),
        "not_current": len(planned - set(current)),
        "backfilled_skus": changed_skus,
        "backfilled_fields": changed_fields,
        "master": str(paths["master"]),
    }
