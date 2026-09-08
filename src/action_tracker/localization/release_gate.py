"""Strict, read-only gate for research-grade Chinese releases.

The gate deliberately validates a projection; it never changes SQLite, the
dictionary, Master, or an Excel workbook.  A caller may use it before a
formal export or before an explicit localization apply.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from ..services.hashing import localization_source_hash
from .policy import has_ordinary_spanish


REQUIRED_ZH_FIELDS = ("name_zh", "cat1_zh", "cat2_zh", "spec_zh", "desc_zh", "details_zh")
PROVENANCE_FIELDS = {
    "name_zh": "name", "cat1_zh": "cat1", "cat2_zh": "cat2", "spec_zh": "spec",
    "desc_zh": "description", "details_zh": "details",
}
APPROVED_REVIEW_STATUSES = frozenset({"VERIFIED", "APPROVED", "HUMAN_REVIEWED"})
CURRENT_FRESHNESS = "CURRENT"


@dataclass(frozen=True)
class ResearchReleaseResult:
    """Serializable result returned by :func:`audit_research_release`."""

    status: str
    counts: Mapping[str, int]
    issues: tuple[str, ...]
    records_checked: int

    @property
    def ok(self) -> bool:
        return self.status == "PASS"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "ok": self.ok,
            "records_checked": self.records_checked,
            "counts": dict(self.counts),
            "issues": list(self.issues),
        }


def audit_research_release(
    records: Iterable[Mapping[str, Any]],
    *,
    expected_skus: Iterable[str] | None = None,
    exported_rows: Iterable[Mapping[str, Any]] | None = None,
    allowed_tokens: set[str] | None = None,
    display_mismatches: Iterable[str] | None = None,
) -> ResearchReleaseResult:
    """Audit records against the non-negotiable research release contract.

    ``records`` are the SQLite projection rows.  ``exported_rows`` is optional
    and, when supplied, is compared only for identity/price/link facts.  The
    function is intentionally side-effect free so it can run against a
    read-only SQLite backup or an in-memory fixture.
    """
    rows = [dict(row) for row in records]
    sku_values = [str(row.get("sku") or row.get("official_sku") or "").strip() for row in rows]
    sku_set = {sku for sku in sku_values if sku}
    duplicate_skus = len(sku_values) - len(sku_set)
    expected = {str(sku).strip() for sku in (expected_skus or ()) if str(sku).strip()}
    exported = [dict(row) for row in (exported_rows or ())]
    exported_skus = {str(row.get("sku") or row.get("编号") or row.get("official_sku") or "").strip() for row in exported}

    counts = {
        "SKU_SET_MISMATCH": 0,
        "FACT_MISMATCH": 0,
        "UNDECLARED_DISPLAY_MISMATCH": len(tuple(display_mismatches or ())),
        "UNAPPROVED_ZH": 0,
        "STALE_ZH": 0,
        "SPANISH_RESIDUAL": 0,
        "SOURCE_HASH_MISMATCH": 0,
        "DUPLICATE_SKU": duplicate_skus,
        "MISSING_REQUIRED_ZH": 0,
    }
    issues: list[str] = []

    if expected and expected != sku_set:
        counts["SKU_SET_MISMATCH"] = len(expected ^ sku_set)
        issues.append("SKU_SET_MISMATCH")
    if exported and exported_skus != sku_set:
        counts["SKU_SET_MISMATCH"] = max(counts["SKU_SET_MISMATCH"], len(exported_skus ^ sku_set))
        issues.append("SKU_SET_MISMATCH:EXPORT")
    if duplicate_skus:
        issues.append("DUPLICATE_SKU")

    for row in rows:
        sku = str(row.get("sku") or row.get("official_sku") or "").strip() or "<EMPTY>"
        field_provenance = row.get("zh_field_provenance") or {}
        missing = [field for field in REQUIRED_ZH_FIELDS if not str(row.get(field) or "").strip()]
        if missing:
            counts["MISSING_REQUIRED_ZH"] += len(missing)
            counts["UNAPPROVED_ZH"] += 1
            issues.extend(f"UNAPPROVED_ZH:{sku}:{field}" for field in missing)
        if field_provenance:
            for field in REQUIRED_ZH_FIELDS:
                metadata = field_provenance.get(PROVENANCE_FIELDS[field]) or {}
                review_status = str(metadata.get("review_status") or "").strip().upper()
                if review_status not in APPROVED_REVIEW_STATUSES:
                    counts["UNAPPROVED_ZH"] += 1
                    issues.append(f"UNAPPROVED_ZH:{sku}:{field}:status={review_status or '<EMPTY>'}")
                freshness = str(metadata.get("freshness_status") or "").strip().upper()
                if freshness != CURRENT_FRESHNESS:
                    counts["STALE_ZH"] += 1
                    issues.append(f"STALE_ZH:{sku}:{field}:{freshness or '<EMPTY>'}")
        else:
            review_status = str(row.get("zh_review_status") or row.get("review_status") or "").strip().upper()
            if review_status not in APPROVED_REVIEW_STATUSES:
                counts["UNAPPROVED_ZH"] += 1
                issues.append(f"UNAPPROVED_ZH:{sku}:status={review_status or '<EMPTY>'}")
            freshness = str(row.get("zh_freshness_status") or row.get("freshness_status") or "").strip().upper()
            if freshness != CURRENT_FRESHNESS:
                counts["STALE_ZH"] += 1
                issues.append(f"STALE_ZH:{sku}:{freshness or '<EMPTY>'}")
        expected_hash = localization_source_hash(row)
        actual_hash = str(row.get("zh_source_hash") or row.get("source_hash") or "").strip()
        if not actual_hash or actual_hash != expected_hash:
            counts["SOURCE_HASH_MISMATCH"] += 1
            issues.append(f"SOURCE_HASH_MISMATCH:{sku}")
        for field in REQUIRED_ZH_FIELDS:
            if has_ordinary_spanish(str(row.get(field) or ""), allowed_tokens=allowed_tokens):
                counts["SPANISH_RESIDUAL"] += 1
                issues.append(f"SPANISH_RESIDUAL:{sku}:{field}")

    if exported:
        by_sku = {str(row.get("sku") or row.get("编号") or row.get("official_sku") or "").strip(): row for row in exported}
        for row in rows:
            sku = str(row.get("sku") or row.get("official_sku") or "").strip()
            other = by_sku.get(sku)
            if not other:
                continue
            comparisons = (
                ("current_price", "current_price", "折后价"),
                ("original_price", "original_price", "原价"),
                ("unit_price", "unit_price", "单价"),
                ("product_url", "product_url", "商品链接"),
            )
            for source_key, _, export_key in comparisons:
                if source_key not in row or export_key not in other:
                    continue
                if _normalized(row.get(source_key)) != _normalized(other.get(export_key)):
                    counts["FACT_MISMATCH"] += 1
                    issues.append(f"FACT_MISMATCH:{sku}:{source_key}")

    if counts["UNDECLARED_DISPLAY_MISMATCH"]:
        issues.append("UNDECLARED_DISPLAY_MISMATCH")
    status = "PASS" if not any(counts.values()) else "FAIL"
    return ResearchReleaseResult(status, counts, tuple(dict.fromkeys(issues)), len(rows))


def _normalized(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
