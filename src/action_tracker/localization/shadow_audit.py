"""Read-only localization audit used by daily-run."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from ..services.hashing import localization_field_source_hash
from ..translation.detail_terminology import find_unmapped_closed_enum_values, load_detail_terminology_rules

FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")
SOURCE_KEYS = {"name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es", "spec": "spec_es", "description": "desc_es", "details": "details_es"}
TARGET_KEYS = {"name": "name_zh", "cat1": "cat1_zh", "cat2": "cat2_zh", "spec": "spec_zh", "description": "desc_zh", "details": "details_zh"}


def run_shadow_audit(records: Iterable[Mapping[str, Any]], *, detail_rules_path: Path | None = None) -> dict[str, Any]:
    """Inspect current source/target fields without translating or writing."""
    rules = load_detail_terminology_rules(detail_rules_path) if detail_rules_path else None
    findings: list[dict[str, Any]] = []
    field_hashes: dict[str, dict[str, str]] = {}
    for record in records:
        sku = str(record.get("sku") or record.get("official_sku") or "").strip()
        field_hashes[sku] = {}
        for field in FIELDS:
            source = str(record.get(SOURCE_KEYS[field]) or "")
            target = str(record.get(TARGET_KEYS[field]) or "")
            field_hashes[sku][field] = localization_field_source_hash(dict(record), field)
            if not source.strip() and target.strip():
                findings.append({"sku": sku, "field": field, "code": "SOURCE_EMPTY_NONEMPTY", "review_only": False})
            elif source.strip() and not target.strip():
                findings.append({"sku": sku, "field": field, "code": "TRANSLATION_MISSING_REVIEW", "review_only": True})
        if rules and str(record.get("details_es") or "").strip():
            context = {key: record.get(key) for key in ("name_es", "cat1_es", "cat2_es")}
            for item in find_unmapped_closed_enum_values(record.get("details_es"), rules, context=context):
                findings.append({"sku": sku, "field": "details", "code": item["code"], "pair_index": item["pair_index"], "source_key": item["source_key"], "source_value": item["source_value"], "review_only": True})
    blocking = [item for item in findings if not item.get("review_only")]
    reviews = [item for item in findings if item.get("review_only")]
    return {
        "enabled": True, "read_only": True, "master_writes": 0, "production_apply": False,
        "sku_count": len(field_hashes), "field_hashes": field_hashes, "findings": findings,
        "blocking_count": len(blocking), "review_count": len(reviews),
        "status": "BLOCKED" if blocking else ("REVIEW_REQUIRED" if reviews else "PASS"),
    }


__all__ = ["run_shadow_audit"]
