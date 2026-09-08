"""Reconcile verified Edge breadcrumb evidence with a committed listing run.

This recovery path exists because browser detail evidence is useful for
complete Spanish content, while several derived projections in historical
PRIMARY rows may still be blank.  It does *not* turn a detail scrape into a
new listing run.  It only repairs categories, the derived Nuevo boolean, and
blank first-seen values under separate, explicit authorities.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..database.integration import database_path, regenerate_compatibility_exports
from ..database.production import ProductionDatabaseError, apply_verified_listing_reconciliation
from ..database.repository import ProductionRepository
from ..products.badges import parse_badges
from .detail_edge_import import (
    EdgeDetailImportError,
    _OK_STATUSES,
    _SKU_RE,
    _clean_text,
    _is_challenge_page,
    _read_payload,
    _validate_parent,
    _validate_records,
    _validate_url,
)


class EdgeListingReconcileError(ValueError):
    """Raised when a category/badge/lifecycle recovery input is not evidence-safe."""


def _category_map(*, run_id: str, candidates: set[str], detail_records: list[dict[str, Any]],
                  completion_path: Path) -> dict[str, dict[str, str]]:
    """Build one category fact for every candidate, rejecting any ambiguity."""
    categories: dict[str, dict[str, str]] = {}
    detail_urls = {str(row["sku"]): str(row["product_url"]) for row in detail_records}
    for row in detail_records:
        cat1 = str(row.get("cat1_es") or "").strip()
        cat2 = str(row.get("cat2_es") or "").strip()
        if cat1 and cat2:
            categories[str(row["sku"])] = {
                "product_url": str(row["product_url"]), "cat1_es": cat1, "cat2_es": cat2,
            }

    payload, raw_rows = _read_payload(completion_path)
    source = str(payload.get("source") or "").strip().upper()
    parent_id = str(payload.get("parent_run_id") or "").strip()
    if source not in {"EDGE_PLUGIN", "EDGE_BROWSER"}:
        raise EdgeListingReconcileError("EDGE_LISTING_RECONCILE_SOURCE_INVALID")
    if parent_id != run_id:
        raise EdgeListingReconcileError("EDGE_LISTING_RECONCILE_PARENT_ID_MISMATCH")
    for raw in raw_rows:
        sku = str(raw.get("sku") or "").strip()
        if not _SKU_RE.fullmatch(sku) or sku not in candidates or sku in categories:
            raise EdgeListingReconcileError(f"EDGE_LISTING_RECONCILE_CATEGORY_SKU_INVALID:{sku}")
        status = str(raw.get("page_status") or raw.get("status") or "").strip().upper()
        title = str(raw.get("page_title") or raw.get("title") or "")
        body_sample = str(raw.get("body_sample") or "")
        if status not in _OK_STATUSES or _is_challenge_page(title, body_sample):
            raise EdgeListingReconcileError(f"EDGE_LISTING_RECONCILE_PAGE_NOT_VERIFIED:{sku}")
        product_url = _validate_url(raw.get("product_url") or raw.get("url"), sku)
        if product_url != detail_urls.get(sku):
            raise EdgeListingReconcileError(f"EDGE_LISTING_RECONCILE_URL_EVIDENCE_MISMATCH:{sku}")
        cat1 = _clean_text(raw.get("cat1_es"), field="cat1_es", sku=sku)
        cat2 = _clean_text(raw.get("cat2_es"), field="cat2_es", sku=sku)
        if not cat1 or not cat2:
            raise EdgeListingReconcileError(f"EDGE_LISTING_RECONCILE_CATEGORY_INCOMPLETE:{sku}")
        categories[sku] = {"product_url": product_url, "cat1_es": cat1, "cat2_es": cat2}
    if set(categories) != candidates:
        missing = sorted(candidates - set(categories))
        extra = sorted(set(categories) - candidates)
        raise EdgeListingReconcileError(
            f"EDGE_LISTING_RECONCILE_CATEGORY_COVERAGE_MISMATCH:missing={','.join(missing)}:extra={','.join(extra)}"
        )
    return categories


def preview_or_apply_edge_listing_reconciliation(
    cfg: dict[str, Any], *, run_id: str, details_path: Path, category_completion_path: Path,
    commit: bool = False,
) -> dict[str, Any]:
    """Validate then optionally apply the auditable listing/lifecycle repair."""
    parent, candidates = _validate_parent(cfg, run_id)
    details_payload, raw_details = _read_payload(details_path)
    details_parent = str(details_payload.get("parent_run_id") or "").strip()
    if details_parent and details_parent != run_id:
        raise EdgeListingReconcileError("EDGE_LISTING_RECONCILE_DETAILS_PARENT_ID_MISMATCH")
    repo = ProductionRepository(database_path(cfg))
    current = {str(row["sku"]): row for row in repo.load_current_export_records()}
    try:
        valid_details = _validate_records(details_payload, raw_details, candidates, current)
    except EdgeDetailImportError as exc:
        raise EdgeListingReconcileError(str(exc)) from exc
    if {str(row["sku"]) for row in valid_details} != candidates:
        raise EdgeListingReconcileError("EDGE_LISTING_RECONCILE_DETAIL_COVERAGE_MISMATCH")
    categories = _category_map(
        run_id=run_id, candidates=candidates, detail_records=valid_details,
        completion_path=Path(category_completion_path),
    )
    lifecycle = repo.load_known_skus()
    plan: list[dict[str, Any]] = []
    for sku in sorted(candidates):
        current_row = current.get(sku)
        state = lifecycle.get(sku) or {}
        first_seen = str(state.get("first_seen_date") or "").strip()[:10]
        if not current_row or not first_seen:
            raise EdgeListingReconcileError(f"EDGE_LISTING_RECONCILE_LIFECYCLE_FIRST_SEEN_MISSING:{sku}")
        badge = bool(parse_badges(current_row.get("raw_tags")).action_new_badge)
        plan.append({
            "sku": sku, "product_url": categories[sku]["product_url"],
            "cat1_es": categories[sku]["cat1_es"], "cat2_es": categories[sku]["cat2_es"],
            "action_new_badge": badge, "first_seen": first_seen,
        })
    result: dict[str, Any] = {
        "status": "PREVIEW", "parent_run_id": run_id, "candidate_count": len(candidates),
        "detail_evidence_rows": len(valid_details), "category_evidence_rows": len(categories),
        "new_badge_yes": sum(bool(row["action_new_badge"]) for row in plan),
        "first_seen_hydration_candidates": sum(not str(current[row["sku"]].get("first_seen") or "").strip() for row in plan),
        "commit": bool(commit),
    }
    if not commit:
    return result
    import_id = f"edge-listing-reconcile_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{len(plan)}"
    evidence_dir = parent / "listing_edge_reconciliations" / import_id
    evidence_dir.mkdir(parents=True, exist_ok=False)
    (evidence_dir / "details_input.json").write_bytes(Path(details_path).read_bytes())
    (evidence_dir / "category_completion_input.json").write_bytes(Path(category_completion_path).read_bytes())
    (evidence_dir / "reconciliation_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    apply_result = apply_verified_listing_reconciliation(
        database_path(cfg), plan, import_id=import_id,
        evidence={"parent_run_id": run_id, "source": "EDGE_PLUGIN", "authority": "breadcrumb+raw_badges+lifecycle"},
    )
    head = repo.current_head()
    sync = regenerate_compatibility_exports(cfg, head) if head else None
    report = {
        **result, **apply_result, "status": "APPLIED", "import_id": import_id,
        "master_sync": sync, "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    (evidence_dir / "reconciliation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return report
