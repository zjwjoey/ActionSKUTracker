"""Contracts and deterministic checks for Stage 5 source-version candidates.

This module is intentionally source-only.  It does not translate, write the
production database, or decide whether a source conflict is true or false.
It provides deterministic provenance and conservative conflict flags so that
human review can make the final Gold decision.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Mapping

from ..services.hashing import localization_source_hash

SOURCE_HASH_ALGORITHM = "localization_source_hash_v1"
SOURCE_HASH_CONTRACT_VERSION = "SOURCE_HASH_V1"
FAMILY_KEY_METHOD = "SPANISH_NAME_HEURISTIC_V1"
SOURCE_FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")
SOURCE_FIELD_KEYS = {
    "name": "name_es",
    "cat1": "cat1_es",
    "cat2": "cat2_es",
    "spec": "spec_es",
    "description": "desc_es",
    "details": "details_es",
}

_POLLUTION = re.compile(
    r"<[^>]+>|\bnull\b|\bundefined\b|añadir a tus favoritos|加入收藏|[\u3400-\u9fff]",
    re.IGNORECASE,
)
_ARTICLE_NUMBER = re.compile(
    # Official snapshots contain tab-, space- and colon-separated variants.
    # Anchor on the full field label; never infer an SKU from another number.
    r"n[uú]mero\s+del\s+art[ií]culo\s*(?:[:：]\s*)?(\d+)", re.IGNORECASE
)
_NUMBER = r"\d+(?:[.,]\d+)?"
_UNIT = (
    r"unidades?|uds?\.?|piezas?|pares?|hojas?|gramos?|kg|g|mg|mcg|µg|ug|"
    r"litros?|l|ml|cl|cm|mm|m|pulgadas?|in|v(?:oltios?)?|w(?:atios?)?|"
    r"mah|gb|mb|denier|días?|meses?|años?|%"
)
_MEASUREMENT = re.compile(rf"(?P<value>{_NUMBER})\s*(?P<unit>{_UNIT})\b", re.IGNORECASE)
_LABEL_VALUE = re.compile(
    rf"(?P<label>[^:;]+):\s*(?P<value>{_NUMBER})\s*(?P<unit>{_UNIT})\b",
    re.IGNORECASE,
)
_SIZE = re.compile(
    r"\b(?:talla|tamaño|size)\s*[:：-]?\s*"
    r"([0-9]{1,3}\s*(?:[-/]\s*[0-9]{1,3})?|"
    r"[xXsSmMlL]{1,4}(?:\s*[-/]\s*[xXsSmMlL]{1,4})?)\b",
    re.IGNORECASE,
)
_EXPLICIT_TYPE = re.compile(
    r"(?:tipo\s+de\s+(?:producto|art[ií]culo)|producto)\s*:\s*([^;,.]+)",
    re.IGNORECASE,
)
_EXPLICIT_MATERIAL = re.compile(r"material\s*:\s*([^;,.]+)", re.IGNORECASE)
_EXPLICIT_BRAND = re.compile(r"\bmarca\s*[:：\t]\s*([^;\n,.]+)", re.IGNORECASE)
_EXPLICIT_MODEL = re.compile(r"\bmodelo\s*[:：\t]\s*([^;\n,.]+)", re.IGNORECASE)


def source_from_mapping(source: Mapping[str, Any]) -> dict[str, str]:
    """Normalize the six source fields without changing their content."""

    return {field: str(source.get(field) or "").strip() for field in SOURCE_FIELDS}


def source_hash(source: Mapping[str, Any]) -> str:
    """Compute the existing V1 hash contract for a six-field source row."""

    normalized = source_from_mapping(source)
    return localization_source_hash(
        {SOURCE_FIELD_KEYS[field]: normalized[field] for field in SOURCE_FIELDS}
    )


def source_quality_issues(source: Mapping[str, Any], sku: str) -> list[str]:
    """Return hard source issues that prevent candidate selection."""

    normalized = source_from_mapping(source)
    issues = [f"EMPTY_{field.upper()}" for field in SOURCE_FIELDS if not normalized[field]]
    issues.extend(
        f"POLLUTED_{field.upper()}"
        for field in SOURCE_FIELDS
        if normalized[field] and _POLLUTION.search(normalized[field])
    )
    article = _ARTICLE_NUMBER.search(normalized["details"])
    if article is None:
        issues.append("DETAIL_SKU_MISSING")
    elif article.group(1) != str(sku).strip():
        issues.append("DETAIL_SKU_MISMATCH")
    return sorted(set(issues))


def _measurement_kind(unit: str) -> str:
    unit = unit.casefold().rstrip(".")
    if unit in {"unidad", "unidades", "ud", "uds", "pieza", "piezas", "par", "pares", "hoja", "hojas"}:
        return "count"
    if unit in {"gramo", "gramos", "g", "kg", "mg", "mcg", "µg", "ug"}:
        return "mass"
    if unit in {"litro", "litros", "l", "ml", "cl"}:
        return "volume"
    if unit in {"cm", "mm", "m", "pulgada", "pulgadas", "in"}:
        return "length"
    if unit in {"v", "voltio", "voltios", "w", "watios", "mah", "gb", "mb"}:
        return "technical"
    if unit in {"denier", "d"}:
        return "denier"
    if unit in {"día", "días", "mes", "meses", "año", "años", "%"}:
        return "other"
    return unit


def _measurements(text: str) -> dict[str, set[tuple[float, str]]]:
    values: dict[str, set[tuple[float, str]]] = {}
    for match in _MEASUREMENT.finditer(text):
        raw_value = match.group("value").replace(",", ".")
        unit = match.group("unit").casefold().rstrip(".")
        try:
            value = float(raw_value)
        except ValueError:
            continue
        values.setdefault(_measurement_kind(unit), set()).add((value, unit))
    return values


def _label_measurements(text: str) -> dict[str, set[tuple[float, str]]]:
    values: dict[str, set[tuple[float, str]]] = {}
    for match in _LABEL_VALUE.finditer(text):
        label = re.sub(r"\s+", " ", match.group("label").casefold()).strip()
        raw_value = match.group("value").replace(",", ".")
        unit = match.group("unit").casefold().rstrip(".")
        try:
            value = float(raw_value)
        except ValueError:
            continue
        values.setdefault(label, set()).add((value, unit))
    return values


def _normal_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", without_marks).strip()


def family_key(source: Mapping[str, Any]) -> str:
    """Create the documented conservative Spanish-name family key."""

    name = _normal_text(str(source.get("name") or ""))
    name = re.sub(rf"{_NUMBER}\s*(?:{_UNIT})\b", " ", name, flags=re.IGNORECASE)
    name = re.sub(r"\b(?:varios|diferentes|distintos)\s+(?:colores|variantes)\b", " ", name)
    name = re.sub(r"[^a-z0-9]+", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def _size_tokens(text: str) -> set[str]:
    return {re.sub(r"\s+", "", match.group(1).casefold()) for match in _SIZE.finditer(text)}


def source_consistency_flags(source: Mapping[str, Any]) -> list[str]:
    """Conservatively flag explicit cross-field contradictions.

    A flag is a review signal, never an automatic rewrite.  The checks focus
    on facts that can be compared mechanically; absence of a flag is not a
    semantic translation approval.
    """

    normalized = source_from_mapping(source)
    flags: set[str] = set()
    spec_measurements = _measurements(normalized["spec"])
    details_measurements = _measurements(normalized["details"])
    description_measurements = _measurements(normalized["description"])
    for kind, spec_values in spec_measurements.items():
        for other in (details_measurements, description_measurements):
            if kind in other and spec_values != other[kind]:
                flags.add("NUMERIC_CONFLICT")
                if {unit for _, unit in spec_values} != {unit for _, unit in other[kind]}:
                    flags.add("UNIT_CONFLICT")
    label_values = _label_measurements(normalized["details"])
    for label, values in label_values.items():
        if len(values) > 1:
            flags.add("NUMERIC_CONFLICT")
            if len({unit for _, unit in values}) > 1:
                flags.add("UNIT_CONFLICT")
    size_sets = [_size_tokens(normalized[field]) for field in ("spec", "details")]
    if all(size_sets) and size_sets[0] != size_sets[1]:
        flags.add("SIZE_RANGE_CONFLICT")
    explicit_types = [_EXPLICIT_TYPE.findall(normalized[field]) for field in ("name", "description", "details")]
    nonempty_types = {(_normal_text(value)) for values in explicit_types for value in values if value.strip()}
    if len(nonempty_types) > 1:
        flags.add("PRODUCT_OBJECT_CONFLICT")
    explicit_materials = [_EXPLICIT_MATERIAL.findall(normalized[field]) for field in ("spec", "description", "details")]
    nonempty_materials = {(_normal_text(value)) for values in explicit_materials for value in values if value.strip()}
    if len(nonempty_materials) > 1:
        flags.add("MATERIAL_CONFLICT")
    for pattern, flag in ((_EXPLICIT_BRAND, "BRAND_CONFLICT"), (_EXPLICIT_MODEL, "MODEL_CONFLICT")):
        explicit_values = {
            _normal_text(value)
            for field in SOURCE_FIELDS
            for value in pattern.findall(normalized[field])
            if value.strip()
        }
        if len(explicit_values) > 1:
            flags.add(flag)
    return sorted(flags)


def consistency_status(flags: list[str]) -> str:
    return "SOURCE_CONFLICT_REVIEW" if flags else "CONSISTENT"
