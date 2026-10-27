"""Optional, source-hash-bound export repair overrides."""
from __future__ import annotations

import csv
import hashlib
import re
import datetime as dt
from pathlib import Path
from typing import Any


HEADERS = (
    "sku", "field", "source_hash", "replacement", "reason", "rule_version",
    "approved_by", "approved_at", "status",
)

_FIELD_TO_SOURCE = {
    "name_zh": "name_es", "cat1_zh": "cat1_es", "cat2_zh": "cat2_es",
    "spec_zh": "spec_es", "desc_zh": "desc_es", "details_zh": "details_es",
    "unit_price_zh": "unit_price",
}
_OUTPUT_TO_TARGET = {"标题": "name_zh", "分类1": "cat1_zh", "分类2": "cat2_zh", "规格": "spec_zh",
                     "描述": "desc_zh", "产品详情": "details_zh"}


class ExportRepairOverrideError(ValueError):
    pass


def prepare_reviewed_candidates(records, current_rows, reviewed_rows, reviewed_sources, *, evidence_ref):
    """Prepare six-field PENDING rows for the existing CSV, never approval.

    Workbook values alone are insufficient: its frozen Spanish field must
    agree with the current same-SKU field. Even then QA/approval is still due.
    """
    def index(rows, key):
        result = {}
        for row in rows:
            sku = str(row.get(key) or "").strip()
            if not sku or sku in result:
                raise ExportRepairOverrideError("REVIEWED_HANDOFF_DUPLICATE_OR_EMPTY_SKU")
            result[sku] = row
        return result
    facts = index(records, "sku"); current = index(current_rows, "编号")
    reviewed = index(reviewed_rows, "编号"); frozen = index(reviewed_sources, "sku")
    if set(facts) != set(current) or set(facts) != set(reviewed) or set(facts) != set(frozen):
        raise ExportRepairOverrideError("REVIEWED_HANDOFF_SKU_SET_MISMATCH")
    if not isinstance(evidence_ref, str) or not evidence_ref.strip():
        raise ExportRepairOverrideError("REVIEWED_HANDOFF_EVIDENCE_REQUIRED")
    candidates = []; blocked = []
    for sku, row in reviewed.items():
        for column, target in _OUTPUT_TO_TARGET.items():
            value = str(row.get(column) or "").strip()
            if value == str(current[sku].get(column) or "").strip():
                continue
            source_field = _FIELD_TO_SOURCE[target]
            source = str(facts[sku].get(source_field) or "").strip()
            old_source = str(frozen[sku].get(source_field) or "").strip()
            code = ("SOURCE_MISSING" if not source else "SOURCE_CHANGED" if source != old_source else
                    "EMPTY_OR_UNCERTAIN_TARGET" if not value or re.search(r"复核|待核|待审核|来源异常|来源冲突", value) else None)
            if code:
                blocked.append({"sku": sku, "field": target, "code": code})
                continue
            candidates.append({"sku": sku, "field": target, "source_hash": field_source_hash(facts[sku], target),
                "replacement": value, "reason": "Reviewed workbook difference; pending field QA and approval; " + evidence_ref,
                "rule_version": "REVIEWED_HANDOFF_V1", "approved_by": "", "approved_at": "", "status": "PENDING"})
    return {"candidates": candidates, "blocked": blocked}


def load_overrides(path: Path | None) -> dict[tuple[str, str], dict[str, str]]:
    if path is None or not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != HEADERS:
            raise ExportRepairOverrideError(f"EXPORT_REPAIR_OVERRIDE_SCHEMA: {path}")
        result: dict[tuple[str, str], dict[str, str]] = {}
        for row in reader:
            normalized = {key: str(row.get(key) or "").strip() for key in HEADERS}
            key = (normalized["sku"], normalized["field"])
            if normalized["status"].upper() != "APPROVED":
                continue
            if not all(key) or normalized["field"] not in _FIELD_TO_SOURCE or not re.fullmatch(r"[a-fA-F0-9]{64}", normalized["source_hash"]):
                raise ExportRepairOverrideError(f"EXPORT_REPAIR_OVERRIDE_IDENTITY: {key}")
            if any(not normalized[k] for k in ("replacement", "reason", "rule_version", "approved_by", "approved_at")):
                raise ExportRepairOverrideError(f"EXPORT_REPAIR_OVERRIDE_APPROVAL_REQUIRED: {key}")
            try:
                dt.datetime.fromisoformat(normalized["approved_at"].replace("Z", "+00:00"))
            except ValueError as exc:
                raise ExportRepairOverrideError(f"EXPORT_REPAIR_OVERRIDE_APPROVAL_TIME: {key}") from exc
            if key in result:
                raise ExportRepairOverrideError(f"EXPORT_REPAIR_OVERRIDE_DUPLICATE: {key[0]}/{key[1]}")
            result[key] = normalized
    return result


def source_hash(fields: dict[str, Any]) -> str:
    """Legacy aggregate hash kept only to identify rows needing reapproval."""
    payload = "\x1f".join(str(fields.get(key) or "").strip() for key in (
        "name_es", "cat1_es", "cat2_es", "spec_es", "desc_es", "details_es", "unit_price",
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def field_source_hash(record: dict[str, Any], field: str) -> str:
    """Bind an override to its own Spanish source field, never the SKU blob."""
    try:
        source_field = _FIELD_TO_SOURCE[str(field)]
    except KeyError as exc:
        raise ExportRepairOverrideError(f"EXPORT_REPAIR_OVERRIDE_UNKNOWN_FIELD: {field}") from exc
    value = str(record.get(source_field) or "").strip()
    return hashlib.sha256((value + "\x1f").encode("utf-8")).hexdigest()


def apply_override(
    *,
    sku: str,
    field: str,
    value: Any,
    record: dict[str, Any],
    overrides: dict[tuple[str, str], dict[str, str]],
) -> tuple[Any, dict[str, str] | None, str]:
    row = overrides.get((str(sku).strip(), str(field).strip()))
    if not row:
        return value, None, "MISSING"
    if not str(record.get(_FIELD_TO_SOURCE[field]) or "").strip():
        return value, row, "SOURCE_MISSING"
    if row["source_hash"] == field_source_hash(record, field):
        return row["replacement"], row, "APPLIED"
    # Aggregate-hash rows were approved under the old contract.  They cannot
    # be safely converted because no historical field-level evidence exists.
    if row["source_hash"] == source_hash(record):
        return value, row, "REAPPROVAL_REQUIRED"
    return value, row, "STALE"
