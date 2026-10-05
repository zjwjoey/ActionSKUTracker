"""Optional, source-hash-bound export repair overrides."""
from __future__ import annotations

import csv
import hashlib
from pathlib import Path
from typing import Any


HEADERS = (
    "sku", "field", "source_hash", "replacement", "reason", "rule_version",
    "approved_by", "approved_at", "status",
)


class ExportRepairOverrideError(ValueError):
    pass


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
            if not all(key) or normalized["status"].upper() != "APPROVED":
                continue
            if key in result:
                raise ExportRepairOverrideError(f"EXPORT_REPAIR_OVERRIDE_DUPLICATE: {key[0]}/{key[1]}")
            result[key] = normalized
    return result


def source_hash(fields: dict[str, Any]) -> str:
    payload = "\x1f".join(str(fields.get(key) or "").strip() for key in (
        "name_es", "cat1_es", "cat2_es", "spec_es", "desc_es", "details_es", "unit_price",
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def apply_override(
    *,
    sku: str,
    field: str,
    value: Any,
    record: dict[str, Any],
    overrides: dict[tuple[str, str], dict[str, str]],
) -> tuple[Any, dict[str, str] | None, bool]:
    row = overrides.get((str(sku).strip(), str(field).strip()))
    if not row:
        return value, None, False
    if row["source_hash"] != source_hash(record):
        return value, row, False
    return row["replacement"], row, True
