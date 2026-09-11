"""Field-level legality checks for official Action facts.

The collector already normalizes known transport artefacts before a record is
stored.  This module is the independent final gate: a non-empty field is not
automatically a valid product fact.  It deliberately recognises only observed,
unambiguous UI/transport pollution and never tries to rewrite Spanish content.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Sequence


LISTING_FACT_FIELDS: tuple[str, ...] = (
    "name_es", "cat1_es", "cat2_es", "spec_es", "product_url", "image_url",
)
DETAIL_FACT_FIELDS: tuple[str, ...] = (
    "name_es", "cat1_es", "cat2_es", "spec_es", "desc_es", "details_es",
    "product_url", "image_url",
)

_HTML_RE = re.compile(r"</?[a-z][^>]*>", re.IGNORECASE)
_PLACEHOLDER_RE = re.compile(r"^(?:null|undefined|none|nan|n/?a|\[object object\])$", re.IGNORECASE)
_UI_LABEL_RE = re.compile(
    r"^(?:"
    r"a(?:ñ|n)adir\s+(?:a\s+)?(?:tus\s+)?favoritos"
    r"|todo\s+de\s+c&c"
    r"|leer\s+m[aá]s"
    r"|add\s+to\s+(?:your\s+)?favorites"
    r"|加入收藏"
    r")\s*[.!]?$",
    re.IGNORECASE,
)
_CATEGORY_ENTRY_LABELS = {"nuevo", "promoción semanal", "promocion semanal"}


@dataclass(frozen=True)
class FieldContentIssue:
    """One non-empty value that cannot be accepted as an official fact."""

    sku: str
    field: str
    code: str
    value: str


def illegal_field_content_code(field: str, value: Any) -> str | None:
    """Return a conservative invalid-content code, or ``None`` when valid.

    Empty values are intentionally not handled here: completeness is a
    separate contract and some official pages genuinely have no standalone
    specification or description.
    """
    text = "" if value is None else str(value).strip()
    if not text:
        return None
    if _PLACEHOLDER_RE.fullmatch(text):
        return "PLACEHOLDER"
    if _HTML_RE.search(text):
        return "HTML_RESIDUE"
    if _UI_LABEL_RE.fullmatch(text):
        return "WEB_UI_LABEL"
    if field in {"cat1_es", "cat2_es"} and text.casefold() in _CATEGORY_ENTRY_LABELS:
        # Nuevo / Promoción semanal are supplementary entry pages, never a
        # product-page breadcrumb category.
        return "SUPPLEMENTARY_ENTRY_AS_CATEGORY"
    if field == "desc_es" and re.match(r"^descripci[oó]n\s*(?:\r?\n|$)", text, re.IGNORECASE):
        return "SECTION_HEADER_RESIDUE"
    if field == "details_es":
        if "::" in text or ": ;" in text or ";;" in text:
            return "DETAIL_SEPARATOR"
        if "\t" in text or "\r" in text or "\n" in text:
            return "DETAIL_WHITESPACE"
    return None


def find_illegal_official_field_content(
    records: Iterable[dict[str, Any]], *, fields: Sequence[str],
) -> list[FieldContentIssue]:
    """Inspect selected fact fields without modifying the supplied records."""
    issues: list[FieldContentIssue] = []
    for record in records:
        sku = str(record.get("sku") or "").strip()
        for field in fields:
            value = record.get(field)
            code = illegal_field_content_code(field, value)
            if code:
                issues.append(FieldContentIssue(sku=sku, field=field, code=code, value=str(value).strip()))
    return issues


def summarize_issues(issues: Iterable[FieldContentIssue]) -> str:
    """Return a stable compact QA message without exposing whole field text."""
    totals: dict[str, int] = {}
    for issue in issues:
        key = f"{issue.field}:{issue.code}"
        totals[key] = totals.get(key, 0) + 1
    return ", ".join(f"{key}={totals[key]}" for key in sorted(totals))
