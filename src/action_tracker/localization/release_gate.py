"""Strict, read-only gate for research-grade Chinese releases.

The gate deliberately validates a projection; it never changes SQLite, the
dictionary, Master, or an Excel workbook.  A caller may use it before a
formal export or before an explicit localization apply.
"""
from __future__ import annotations

from dataclasses import dataclass
import csv
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..services.hashing import localization_source_hash
import re
import json


REQUIRED_ZH_FIELDS = ("name_zh", "cat1_zh", "cat2_zh", "spec_zh", "desc_zh", "details_zh")
PROVENANCE_FIELDS = {
    "name_zh": "name", "cat1_zh": "cat1", "cat2_zh": "cat2", "spec_zh": "spec",
    "desc_zh": "description", "details_zh": "details",
}
APPROVED_REVIEW_STATUSES = frozenset({"VERIFIED", "APPROVED", "HUMAN_REVIEWED"})
CURRENT_FRESHNESS = "CURRENT"
_SPANISH_WORDS = {
    "para", "con", "sin", "varios", "varias", "diferentes", "unidades", "unidad", "colores",
    "negro", "blanco", "rojo", "azul", "verde", "de", "del", "la", "el", "y", "o", "en",
    "tipo", "tamaño", "material", "contenido", "cantidad", "incluye", "lavable", "resistente",
    "hogar", "limpieza", "cocina", "juguetes", "mascotas", "cuidado", "personal", "oficina",
    "papelería", "ropa", "moda", "viajes", "jardín", "decoración", "iluminación", "audio",
    "accesorios", "pilas", "muebles", "maquillaje", "bebé", "alimentación", "galletas", "bebidas",
    "perro", "salud", "almacenamiento", "cartuchos", "tinta", "cables", "divisores", "baño",
    "artículos", "deportivos", "fiesta", "papel", "escolar", "fácil", "aplicar", "más", "leer",
    "caja", "plástico", "producto", "juego", "juguete", "botella", "set", "crema", "gel",
}
_WORD_RE = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+")


@dataclass(frozen=True)
class ResearchReleaseResult:
    """Serializable result returned by :func:`audit_research_release`."""

    status: str
    counts: Mapping[str, int]
    issues: tuple[str, ...]
    records_checked: int
    exceptions: tuple[str, ...] = ()

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
            "exceptions": list(self.exceptions),
        }


def audit_research_release(
    records: Iterable[Mapping[str, Any]],
    *,
    expected_skus: Iterable[str] | None = None,
    exported_rows: Iterable[Mapping[str, Any]] | None = None,
    allowed_tokens: set[str] | None = None,
    display_mismatches: Iterable[str] | None = None,
    explicit_exceptions: Iterable[Mapping[str, Any]] | None = None,
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
                # Active PRIMARY stores field values in localization_fields;
                # freshness/approval audit columns may still live on the
                # legacy aggregate projection.  Fall back to the aggregate
                # value only when the field-level column is absent, never
                # overwrite an explicit field-level decision.
                review_status = str(
                    metadata.get("review_status")
                    or row.get("zh_review_status")
                    or row.get("review_status")
                    or ""
                ).strip().upper()
                if review_status not in APPROVED_REVIEW_STATUSES:
                    counts["UNAPPROVED_ZH"] += 1
                    issues.append(f"UNAPPROVED_ZH:{sku}:{field}:status={review_status or '<EMPTY>'}")
                freshness = str(
                    metadata.get("freshness_status")
                    or row.get("zh_freshness_status")
                    or row.get("freshness_status")
                    or ""
                ).strip().upper()
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
            if _has_release_spanish_residual(str(row.get(field) or ""), allowed_tokens=allowed_tokens):
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
    exception_ids = _validated_exception_ids(explicit_exceptions or ())
    unique_issues = tuple(dict.fromkeys(issues))
    suppressed = tuple(issue for issue in unique_issues if issue in exception_ids)
    effective_issues = tuple(issue for issue in unique_issues if issue not in exception_ids)
    counts["EXPLICIT_EXCEPTION"] = len(suppressed)
    blocking_counts = {key: value for key, value in counts.items() if key != "EXPLICIT_EXCEPTION"}
    status = "PASS" if not any(blocking_counts.values()) or not effective_issues and suppressed else "FAIL"
    return ResearchReleaseResult(status, counts, effective_issues, len(rows), suppressed)


def load_allowed_tokens(dictionary_root: Path) -> set[str]:
    """Load reviewed brand/technical tokens without treating normal Spanish as safe."""
    tokens: set[str] = set()
    for filename, columns in (
        ("brand_dictionary.csv", ("canonical_name", "aliases_es")),
        ("tech_token_dictionary.csv", ("term_es", "term", "token")),
    ):
        path = Path(dictionary_root) / filename
        if not path.exists():
            continue
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                status = str(row.get("review_status") or "").strip().upper()
                if status and status not in {"HUMAN_REVIEWED", "VERIFIED", "CAT1_CONFIRMED"}:
                    continue
                for column in columns:
                    for token in str(row.get(column) or "").replace("|", ",").split(","):
                        token = token.strip()
                        if token:
                            tokens.add(token)
                            tokens.update(part for part in token.split() if part)
    return tokens


def load_explicit_exceptions(path: Path) -> list[dict[str, str]]:
    """Load only evidence-backed, expiring release exceptions.

    Missing files deliberately mean no exceptions.  The gate still validates
    every entry's issue id, approver, evidence and expiry before suppressing
    anything; this is not a general warning allowlist.
    """
    path = Path(path)
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("exceptions", [])
    if not isinstance(payload, list):
        raise ValueError("RELEASE_EXCEPTIONS_SCHEMA_INVALID")
    result: list[dict[str, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("RELEASE_EXCEPTION_ENTRY_INVALID")
        result.append({key: str(item.get(key) or "").strip() for key in ("issue_id", "approved_by", "evidence", "expires_at")})
    return result


def _validated_exception_ids(exceptions: Iterable[Mapping[str, Any]]) -> set[str]:
    """Return only explicit, non-expired exceptions with audit evidence."""
    from datetime import date
    accepted: set[str] = set()
    today = date.today().isoformat()
    for item in exceptions:
        issue_id = str(item.get("issue_id") or "").strip()
        approved_by = str(item.get("approved_by") or "").strip()
        evidence = str(item.get("evidence") or "").strip()
        expires_at = str(item.get("expires_at") or "").strip()
        if issue_id and approved_by and evidence and expires_at >= today:
            accepted.add(issue_id)
    return accepted


def _has_release_spanish_residual(value: str, *, allowed_tokens: set[str] | None) -> bool:
    """Detect known ordinary Spanish while allowing reviewed brand/model text."""
    allowed = {token.lower() for token in (allowed_tokens or set())}
    for word in _WORD_RE.findall(value or ""):
        lower = word.lower()
        if lower in allowed or lower in {"usb", "usb-c", "led", "lcd", "diy", "fsc"}:
            continue
        if lower in _SPANISH_WORDS:
            return True
    return False


def _normalized(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
