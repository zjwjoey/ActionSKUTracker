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
from typing import Any, Mapping

from ..database.integration import database_path, regenerate_compatibility_exports
from ..database.production import ProductionDatabaseError, apply_verified_listing_reconciliation
from ..database.repository import ProductionRepository
from ..products.badges import parse_badges
from .detail_edge_import import (
    EdgeDetailImportError,
    _OK_STATUSES,
    _SKU_RE,
    _clean_text,
    _load_deferred_detail_queue,
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


def _category_key(value: Any) -> str:
    """Normalize category text only for conflict comparison."""
    return " ".join(str(value or "").split()).casefold()


def _build_deferred_category_plan(
    *, current: Mapping[str, Mapping[str, Any]], lifecycle: Mapping[str, Mapping[str, Any]],
    detail_records: list[dict[str, Any]], approved_conflict_skus: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build a category-only repair plan and isolate conflicting breadcrumbs.

    Deferred Edge evidence may safely fill a blank category, but it must not
    silently replace a non-blank Master category.  Conflicting cat1/cat2
    values are returned for review; callers may commit only a conflict-free
    plan through :func:`apply_verified_listing_reconciliation`.
    """
    plan: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    approved = {str(sku).strip() for sku in (approved_conflict_skus or set()) if str(sku).strip()}
    for row in detail_records:
        sku = str(row.get("sku") or "").strip()
        current_row = current.get(sku)
        state = lifecycle.get(sku) or {}
        if not current_row:
            raise EdgeListingReconcileError(f"DEFERRED_CATEGORY_SKU_NOT_CURRENT:{sku}")
        cat1 = str(row.get("cat1_es") or "").strip()
        cat2 = str(row.get("cat2_es") or "").strip()
        if not cat1 or not cat2:
            raise EdgeListingReconcileError(f"DEFERRED_CATEGORY_EVIDENCE_INCOMPLETE:{sku}")
        existing_cat1 = str(current_row.get("cat1_es") or "").strip()
        existing_cat2 = str(current_row.get("cat2_es") or "").strip()
        mismatch_fields: list[str] = []
        if existing_cat1 and _category_key(existing_cat1) != _category_key(cat1):
            mismatch_fields.append("cat1_es")
        if existing_cat2 and _category_key(existing_cat2) != _category_key(cat2):
            mismatch_fields.append("cat2_es")
        if mismatch_fields:
            conflict = {
                "sku": sku,
                "fields": mismatch_fields,
                "master": {"cat1_es": existing_cat1, "cat2_es": existing_cat2},
                "edge": {"cat1_es": cat1, "cat2_es": cat2},
                "product_url": str(row.get("product_url") or ""),
            }
            if sku not in approved:
                conflicts.append(conflict)
                continue
        first_seen = str(state.get("first_seen_date") or current_row.get("first_seen") or "").strip()[:10]
        if len(first_seen) != 10:
            raise EdgeListingReconcileError(f"DEFERRED_CATEGORY_FIRST_SEEN_MISSING:{sku}")
        accepted_conflict = bool(mismatch_fields and sku in approved)
        plan.append({
            "sku": sku,
            "product_url": str(row.get("product_url") or ""),
            "cat1_es": cat1 if accepted_conflict else (existing_cat1 or cat1),
            "cat2_es": cat2 if accepted_conflict else (existing_cat2 or cat2),
            "action_new_badge": bool(parse_badges(current_row.get("raw_tags")).action_new_badge),
            "first_seen": first_seen,
            "official_conflict_approved": accepted_conflict,
        })
    return plan, conflicts


def preview_or_apply_deferred_category_reconciliation(
    cfg: dict[str, Any], *, run_id: str, input_path: Path, commit: bool = False,
    approved_conflict_skus: set[str] | None = None,
) -> dict[str, Any]:
    """Preview/apply category breadcrumbs for a committed deferred backlog.

    This route is intentionally separate from detail import: it accepts a
    normal QA-passing parent, uses ``detail_backlog.csv`` as the queue, fills
    only blank categories, and refuses any non-blank category conflict.
    """
    parent, _ = _validate_parent(
        cfg, run_id, for_staging=True, require_product_updates=False,
    )
    report = json.loads((parent / "run_report.json").read_text(encoding="utf-8"))
    if report.get("dry_run") or report.get("commit_status") != "FULL_COMMIT":
        raise EdgeListingReconcileError("DEFERRED_CATEGORY_PARENT_NOT_COMMITTED")
    deferred, queue_source = _load_deferred_detail_queue(parent)
    if not deferred:
        raise EdgeListingReconcileError("DEFERRED_CATEGORY_QUEUE_EMPTY")
    payload, raw_rows = _read_payload(Path(input_path))
    if payload.get("parent_run_id") and str(payload["parent_run_id"]).strip() != run_id:
        raise EdgeListingReconcileError("DEFERRED_CATEGORY_PARENT_ID_MISMATCH")
    supplied = {str(row.get("sku") or "").strip() for row in raw_rows}
    if supplied != deferred:
        missing = sorted(deferred - supplied)
        extra = sorted(supplied - deferred)
        raise EdgeListingReconcileError(
            f"DEFERRED_CATEGORY_QUEUE_COVERAGE_MISMATCH:missing={','.join(missing)}:extra={','.join(extra)}"
        )
    repo = ProductionRepository(database_path(cfg))
    current = {str(row["sku"]): row for row in repo.load_current_export_records()}
    try:
        valid_details = _validate_records(
            payload, raw_rows, deferred, current,
            allow_already_enriched=True,
        )
    except EdgeDetailImportError as exc:
        raise EdgeListingReconcileError(str(exc)) from exc
    lifecycle = repo.load_known_skus()
    approved = {str(sku).strip() for sku in (approved_conflict_skus or set()) if str(sku).strip()}
    plan, conflicts = _build_deferred_category_plan(
        current=current, lifecycle=lifecycle, detail_records=valid_details,
        approved_conflict_skus=approved,
    )
    approved_in_plan = {
        str(row["sku"]) for row in plan if row.get("official_conflict_approved")
    }
    unused_approvals = sorted(approved - approved_in_plan)
    if unused_approvals:
        raise EdgeListingReconcileError(
            f"DEFERRED_CATEGORY_APPROVAL_UNUSED:{','.join(unused_approvals)}"
        )
    result: dict[str, Any] = {
        "status": "REVIEW_REQUIRED" if conflicts else "PREVIEW",
        "parent_run_id": run_id,
        "queue_source": queue_source,
        "queue_count": len(deferred),
        "input_rows": len(raw_rows),
        "valid_rows": len(valid_details),
        "ready_rows": len(plan),
        "conflict_count": len(conflicts),
        "conflicts": conflicts,
        "approved_conflict_skus": sorted(approved),
        "approved_conflicts_applied": sorted(approved_in_plan),
        "commit": bool(commit),
    }
    if conflicts:
        if commit:
            raise EdgeListingReconcileError(
                f"DEFERRED_CATEGORY_CONFLICTS:{[item['sku'] for item in conflicts]}"
            )
        return result
    if not commit:
        return result
    import_id = f"deferred-category-reconcile_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{len(plan)}"
    evidence_dir = parent / "deferred_category_reconciliations" / import_id
    evidence_dir.mkdir(parents=True, exist_ok=False)
    (evidence_dir / "input.json").write_bytes(Path(input_path).read_bytes())
    (evidence_dir / "plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    apply_result = apply_verified_listing_reconciliation(
        database_path(cfg), plan, import_id=import_id,
        evidence={"parent_run_id": run_id, "source": "EDGE_PLUGIN",
                  "authority": "breadcrumb+raw_badges+lifecycle",
                  "queue_source": queue_source,
                  "approved_conflict_skus": sorted(approved_in_plan)},
    )
    head = repo.current_head()
    sync = regenerate_compatibility_exports(cfg, head) if head else None
    final = {
        **result, **apply_result, "status": "APPLIED", "import_id": import_id,
        "master_sync": sync, "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    (evidence_dir / "reconciliation_report.json").write_text(
        json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return final


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
