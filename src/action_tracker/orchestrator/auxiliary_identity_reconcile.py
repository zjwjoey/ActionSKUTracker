"""Controlled identity recovery for verified auxiliary-only product pages."""
from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..database.integration import regenerate_compatibility_exports
from ..database.integration import database_path
from ..database.production import ProductionDatabaseError, apply_verified_auxiliary_identity_updates
from ..database.repository import ProductionRepository
from .detail_edge_import import (
    _OK_STATUSES,
    _is_challenge_page,
    _read_payload,
    _validate_parent,
    _validate_url,
)


class AuxiliaryIdentityReconcileError(ValueError):
    """Input or parent-observation validation failed."""


_SKU_RE = re.compile(r"^\d+$")


def _text(value: Any, *, field: str, sku: str, required: bool = False) -> str:
    if value in (None, ""):
        if required:
            raise AuxiliaryIdentityReconcileError(f"AUXILIARY_IDENTITY_{field.upper()}_MISSING:{sku}")
        return ""
    if not isinstance(value, str):
        raise AuxiliaryIdentityReconcileError(f"AUXILIARY_IDENTITY_{field.upper()}_NOT_TEXT:{sku}")
    result = value.strip()
    if "\ufffd" in result:
        raise AuxiliaryIdentityReconcileError(f"AUXILIARY_IDENTITY_ENCODING_ERROR:{sku}:{field}")
    return result


def _presence_flags(parent: Path) -> dict[str, dict[str, str]]:
    path = parent / "presence_evidence.csv"
    if not path.exists():
        raise AuxiliaryIdentityReconcileError("AUXILIARY_IDENTITY_PRESENCE_EVIDENCE_MISSING")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return {str(row.get("sku") or "").strip(): dict(row) for row in csv.DictReader(handle)}


def _validate_records(
    *, run_id: str, parent: Path, payload: dict[str, Any], raw_rows: list[dict[str, Any]],
    current: dict[str, dict[str, Any]], candidates: set[str],
) -> list[dict[str, Any]]:
    if str(payload.get("source") or "").strip().upper() not in {"EDGE_PLUGIN", "EDGE_BROWSER"}:
        raise AuxiliaryIdentityReconcileError("AUXILIARY_IDENTITY_SOURCE_INVALID")
    if str(payload.get("parent_run_id") or "").strip() != run_id:
        raise AuxiliaryIdentityReconcileError("AUXILIARY_IDENTITY_PARENT_ID_REQUIRED")
    evidence = _presence_flags(parent)
    seen: set[str] = set()
    valid: list[dict[str, Any]] = []
    for raw in raw_rows:
        sku = str(raw.get("sku") or "").strip()
        if not _SKU_RE.fullmatch(sku) or sku in seen:
            raise AuxiliaryIdentityReconcileError(f"AUXILIARY_IDENTITY_SKU_INVALID:{sku}")
        seen.add(sku)
        if sku not in current:
            raise AuxiliaryIdentityReconcileError(f"AUXILIARY_IDENTITY_SKU_NOT_CURRENT:{sku}")
        if sku not in candidates:
            raise AuxiliaryIdentityReconcileError(f"AUXILIARY_IDENTITY_SKU_NOT_DETAIL_CANDIDATE:{sku}")
        presence = evidence.get(sku) or {}
        if str(presence.get("source_flag") or "").strip().upper() != "AUXILIARY_ONLY":
            raise AuxiliaryIdentityReconcileError(f"AUXILIARY_IDENTITY_SOURCE_NOT_AUXILIARY:{sku}")
        if str(presence.get("observation_valid") or "").strip().lower() not in {"true", "1", "yes"}:
            raise AuxiliaryIdentityReconcileError(f"AUXILIARY_IDENTITY_OBSERVATION_INVALID:{sku}")
        status = str(raw.get("page_status") or raw.get("status") or "").strip().upper()
        title = str(raw.get("page_title") or raw.get("title") or "")
        body_sample = str(raw.get("body_sample") or "")
        if status not in _OK_STATUSES or _is_challenge_page(title, body_sample):
            raise AuxiliaryIdentityReconcileError(f"AUXILIARY_IDENTITY_PAGE_NOT_VERIFIED:{sku}")
        row = {
            "sku": sku,
            "product_url": _validate_url(raw.get("product_url") or raw.get("url"), sku),
            "name_es": _text(raw.get("name_es"), field="name_es", sku=sku, required=True),
            "cat1_es": _text(raw.get("cat1_es"), field="cat1_es", sku=sku, required=True),
            "cat2_es": _text(raw.get("cat2_es"), field="cat2_es", sku=sku, required=True),
            "spec_es": _text(raw.get("spec_es"), field="spec_es", sku=sku, required=True),
        }
        # Auxiliary evidence may fill a blank identity projection, but it may
        # never silently replace an already observed official fact.  A
        # disagreement is a review candidate, not an automatic guess.
        for field in ("name_es", "cat1_es", "cat2_es", "spec_es"):
            existing = str(current.get(sku, {}).get(field) or "").strip()
            proposed = row[field]
            if existing and existing.casefold() != proposed.casefold():
                raise AuxiliaryIdentityReconcileError(
                    f"AUXILIARY_IDENTITY_CONFLICT_REVIEW_REQUIRED:{sku}:{field}"
                )
        image = _text(raw.get("image_url"), field="image_url", sku=sku)
        if image:
            row["image_url"] = image
        valid.append(row)
    return valid


def preview_or_apply_auxiliary_identity_reconciliation(
    cfg: dict[str, Any], *, run_id: str, input_path: Path, commit: bool = False,
) -> dict[str, Any]:
    """Preview/apply a finite, verified auxiliary identity recovery batch."""
    parent, candidates = _validate_parent(cfg, run_id)
    payload, raw_rows = _read_payload(Path(input_path))
    repo = ProductionRepository(database_path(cfg))
    current = {str(row["sku"]): row for row in repo.load_current_export_records()}
    valid = _validate_records(
        run_id=run_id, parent=parent, payload=payload, raw_rows=raw_rows,
        current=current, candidates=candidates,
    )
    result: dict[str, Any] = {
        "status": "PREVIEW", "entry": "AUXILIARY_IDENTITY_RECONCILE",
        "parent_run_id": run_id, "input_rows": len(raw_rows),
        "valid_rows": len(valid), "candidate_count": len(candidates), "commit": bool(commit),
    }
    if not commit:
        return result
    import_id = (
        f"auxiliary-identity-reconcile_"
        f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{len(valid)}"
    )
    evidence_dir = parent / "auxiliary_identity_reconciliations" / import_id
    evidence_dir.mkdir(parents=True, exist_ok=False)
    (evidence_dir / "input.json").write_bytes(Path(input_path).read_bytes())
    apply_result = apply_verified_auxiliary_identity_updates(
        database_path(cfg), valid, import_id=import_id,
        evidence={"parent_run_id": run_id, "source": str(payload.get("source")).upper(),
                  "source_flag": "AUXILIARY_ONLY", "authority": "verified_edge_product_page"},
    )
    head = repo.current_head()
    sync_error = None
    try:
        sync = regenerate_compatibility_exports(cfg, head) if head else None
    except Exception as exc:  # database write is already committed; preserve an explicit retry state
        sync = {"status": "PENDING", "error": f"{type(exc).__name__}: {exc}"}
        sync_error = sync["error"]
    final = {**result, **apply_result,
             "status": "APPLIED" if sync_error is None else "APPLIED_MASTER_SYNC_PENDING",
             "import_id": import_id, "master_sync": sync,
             "finished_at": datetime.now(timezone.utc).isoformat()}
    (evidence_dir / "reconciliation_report.json").write_text(
        json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return final
