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

from ..products import updater
from ..services.access import AccessController
from ..services.browser import BrowserSession
from ..services.runtime import RunLock, madrid_now


_DETAIL_REASONS = {"NEW", "REAPPEARED", "MISSING_FIELD", "DETAIL_REFRESH"}


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
