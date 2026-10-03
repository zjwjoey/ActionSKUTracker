"""Field and identity validation for experimental detail extraction."""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any
from urllib.parse import urlparse

from scrapling import Selector

from ...services.browser import is_challenge
from .parser import FIELD_NAMES


_URL_SKU = re.compile(r"/p/(\d+)(?:/|$)", re.I)
_ARTICLE_SKU = re.compile(r"N[uú]mero\s+del\s+art[ií]culo\s*:\s*(\d+)", re.I)
_DETAIL_PAIR = re.compile(r"^\s*[^:;]+\s*:\s*[^;]+\s*$")
_SPEC_CONTAMINATION = re.compile(
    r"€|descripci[oó]n|especificaciones|sostenibilidad|productos relacionados|categor[ií]as recomendadas",
    re.I,
)
_DESC_CONTAMINATION = re.compile(
    r"n[uú]mero del art[ií]culo|especificaciones|productos relacionados|"
    r"categor[ií]as recomendadas|sostenibilidad",
    re.I,
)
_CHALLENGE_HTML = re.compile(
    r"cf-challenge|cf-turnstile|captcha|verify you are human|checking your browser|"
    r"just a moment|un momento",
    re.I,
)
_HOME_TITLE = re.compile(r"^action espa(?:ñ|n)a\s*:\s*peque(?:ñ|n)os precios", re.I)


@dataclass
class ValidationResult:
    valid: bool
    reasons: list[str] = field(default_factory=list)
    invalid_fields: list[str] = field(default_factory=list)
    field_sources: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "reasons": list(self.reasons),
            "invalid_fields": list(self.invalid_fields),
            "field_sources": dict(self.field_sources),
        }


def looks_like_challenge(html: str, title: str = "") -> bool:
    if is_challenge(title) or _CHALLENGE_HTML.search(title or ""):
        return True
    try:
        selector = Selector(html or "<html></html>")
        body = selector.css("body").first
        text = str(body.get_all_text(separator=" ", strip=True))[:3000] if body else ""
    except Exception:
        text = html[:3000]
    return bool(_CHALLENGE_HTML.search(text))


def validate_detail(
    row: dict[str, Any],
    *,
    target_sku: str,
    url: str,
    html: str,
    field_sources: dict[str, str] | None = None,
    page_title: str = "",
) -> ValidationResult:
    """Validate extraction without copying or inferring any field values."""
    sources = dict(field_sources or row.get("field_sources") or {})
    reasons: list[str] = []
    invalid_fields: set[str] = set()
    target = str(target_sku).strip()
    url_match = _URL_SKU.search(url or "")
    url_sku = url_match.group(1) if url_match else ""
    row_sku = str(row.get("sku") or "").strip()

    if not url_sku or url_sku != target:
        reasons.append("URL_SKU_MISMATCH")
        invalid_fields.add("sku")
    if not row_sku or row_sku != target:
        reasons.append("SKU_MISMATCH")
        invalid_fields.add("sku")

    details = str(row.get("details_es") or "")
    article_match = _ARTICLE_SKU.search(details)
    if article_match and article_match.group(1) != target:
        reasons.append("DETAIL_SKU_MISMATCH")
        invalid_fields.update({"sku", "details_es"})

    if looks_like_challenge(html, page_title or str(row.get("page_title") or "")):
        reasons.append("CHALLENGE_PAGE")
        invalid_fields.update(FIELD_NAMES)

    name = str(row.get("name_es") or "").strip()
    if not name:
        reasons.append("NAME_EMPTY")
        invalid_fields.add("name_es")
    elif name.casefold() == "www.action.com" or _HOME_TITLE.search(name):
        reasons.append("NAME_NOT_PRODUCT_TITLE")
        invalid_fields.add("name_es")
    elif re.search(r"captcha|just a moment|un momento|cloudflare", name, re.I):
        reasons.append("NAME_CHALLENGE_TITLE")
        invalid_fields.add("name_es")

    spec = str(row.get("spec_es") or "").strip()
    if spec and _SPEC_CONTAMINATION.search(spec):
        reasons.append("SPEC_CONTAMINATION")
        invalid_fields.add("spec_es")

    desc = str(row.get("desc_es") or "").strip()
    if desc and _DESC_CONTAMINATION.search(desc):
        reasons.append("DESCRIPTION_CONTAMINATION")
        invalid_fields.add("desc_es")

    if details:
        segments = [segment.strip() for segment in details.split(";") if segment.strip()]
        if not segments or any(not _DETAIL_PAIR.match(segment) for segment in segments):
            reasons.append("DETAILS_NOT_KEY_VALUE")
            invalid_fields.add("details_es")

    for field_name in FIELD_NAMES:
        source = sources.get(field_name)
        if source not in {"PRIMARY", "ADAPTIVE", "NOT_FOUND", "INVALID"}:
            reasons.append(f"FIELD_SOURCE_MISSING:{field_name}")
            invalid_fields.add(field_name)
            sources[field_name] = "INVALID"
    for name_field in invalid_fields:
        if name_field in sources:
            sources[name_field] = "INVALID"

    return ValidationResult(
        valid=not reasons,
        reasons=list(dict.fromkeys(reasons)),
        invalid_fields=sorted(invalid_fields),
        field_sources=sources,
    )
