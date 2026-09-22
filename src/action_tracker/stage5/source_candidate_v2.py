"""Source-only checks for Stage 5 candidates.

This module never translates, applies, or writes production data. It emits
source provenance, family keys, and conservative conflict flags for review.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Mapping

from ..services.hashing import localization_field_source_hash, localization_source_hash

SOURCE_HASH_ALGORITHM = "localization_source_hash_v1"
SOURCE_HASH_CONTRACT_VERSION = "SOURCE_HASH_V1"
FAMILY_KEY_METHOD = "SPANISH_NAME_HEURISTIC_V1"
SOURCE_FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")
SOURCE_FIELD_KEYS = {
    "name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es", "spec": "spec_es",
    "description": "desc_es", "details": "details_es",
}

_POLLUTION = re.compile(r"<[^>]+>|\bnull\b|\bundefined\b|añadir a tus favoritos|加入收藏|[\u3400-\u9fff]", re.IGNORECASE)
_ARTICLE_NUMBER = re.compile(r"n[uú]mero\s+del\s+art[ií]culo\s*(?:[:：]\s*)?(\d+)", re.IGNORECASE)
_NUMBER = r"\d+(?:[.,]\d+)?"
_UNIT = r"unidades?|uds?\.?|piezas?|pares?|hojas?|gramos?|kg|g|mg|mcg|µg|ug|litros?|l|ml|cl|cm|mm|m|pulgadas?|in|v(?:oltios?)?|w(?:atios?)?|mah|gb|mb|denier|días?|meses?|años?|%"
_MEASUREMENT = re.compile(rf"(?P<value>{_NUMBER})\s*(?P<unit>{_UNIT})\b", re.IGNORECASE)
_LABEL_VALUE = re.compile(rf"(?P<label>[^:;]+):\s*(?P<value>{_NUMBER})\s*(?P<unit>{_UNIT})\b", re.IGNORECASE)
_SIZE = re.compile(r"\b(?:talla|tamaño|size)\s*[:：-]?\s*([0-9]{1,3}\s*(?:[-/]\s*[0-9]{1,3})?|[xXsSmMlL]{1,4}(?:\s*[-/]\s*[xXsSmMlL]{1,4})?)\b", re.IGNORECASE)
_EXPLICIT_TYPE = re.compile(r"(?:tipo\s+de\s+(?:producto|art[ií]culo)|producto)\s*:\s*([^;,.]+)", re.IGNORECASE)
_EXPLICIT_MATERIAL = re.compile(r"material\s*:\s*([^;,.]+)", re.IGNORECASE)
_EXPLICIT_BRAND = re.compile(r"\bmarca\s*[:：\t]\s*([^;\n,.]+)", re.IGNORECASE)
_EXPLICIT_MODEL = re.compile(r"\bmodelo\s*[:：\t]\s*([^;\n,.]+)", re.IGNORECASE)


def source_from_mapping(source: Mapping[str, Any]) -> dict[str, str]:
    return {field: str(source.get(field) or "").strip() for field in SOURCE_FIELDS}


def source_hash(source: Mapping[str, Any]) -> str:
    normalized = source_from_mapping(source)
    return localization_source_hash({SOURCE_FIELD_KEYS[field]: normalized[field] for field in SOURCE_FIELDS})


def field_source_hash(source: Mapping[str, Any], field: str) -> str:
    normalized = source_from_mapping(source)
    return localization_field_source_hash({SOURCE_FIELD_KEYS[key]: normalized[key] for key in SOURCE_FIELDS}, field)


def candidate_source_provenance(source: Mapping[str, Any], field: str) -> dict[str, str]:
    """Return the mandatory provenance envelope for a new field candidate.

    ``source_hash()`` remains available for legacy six-field records and
    patch identity. New Stage5/Stage6 candidates must use this helper (or the
    equivalent field hash) so unrelated Spanish fields cannot invalidate or
    refresh the candidate.
    """
    if field not in SOURCE_FIELDS:
        raise ValueError(f"UNKNOWN_SOURCE_FIELD:{field}")
    normalized = source_from_mapping(source)
    return {
        "hash_scope": "field",
        "source_field": field,
        "source_spanish_value": normalized[field],
        "source_hash": field_source_hash(normalized, field),
    }


def source_quality_issues(source: Mapping[str, Any], sku: str) -> list[str]:
    normalized = source_from_mapping(source)
    issues = [f"EMPTY_{field.upper()}" for field in SOURCE_FIELDS if not normalized[field]]
    issues.extend(f"POLLUTED_{field.upper()}" for field in SOURCE_FIELDS if normalized[field] and _POLLUTION.search(normalized[field]))
    article = _ARTICLE_NUMBER.search(normalized["details"])
    if article is None:
        issues.append("DETAIL_SKU_MISSING")
    elif article.group(1) != str(sku).strip():
        issues.append("DETAIL_SKU_MISMATCH")
    return sorted(set(issues))


def _measurement_kind(unit: str) -> str:
    unit = unit.casefold().rstrip(".")
    if unit in {"unidad", "unidades", "ud", "uds", "pieza", "piezas", "par", "pares", "hoja", "hojas"}: return "count"
    if unit in {"gramo", "gramos", "g", "kg", "mg", "mcg", "µg", "ug"}: return "mass"
    if unit in {"litro", "litros", "l", "ml", "cl"}: return "volume"
    if unit in {"cm", "mm", "m", "pulgada", "pulgadas", "in"}: return "length"
    if unit in {"v", "voltio", "voltios", "w", "watios", "mah", "gb", "mb"}: return "technical"
    if unit in {"denier", "d"}: return "denier"
    if unit in {"día", "días", "mes", "meses", "año", "años", "%"}: return "other"
    return unit


def _measurements(text: str) -> dict[str, set[tuple[float, str]]]:
    values: dict[str, set[tuple[float, str]]] = {}
    for match in _MEASUREMENT.finditer(text):
        try: value = float(match.group("value").replace(",", "."))
        except ValueError: continue
        unit = match.group("unit").casefold().rstrip(".")
        values.setdefault(_measurement_kind(unit), set()).add((value, unit))
    return values


def _label_measurements(text: str) -> dict[str, set[tuple[float, str]]]:
    values: dict[str, set[tuple[float, str]]] = {}
    for match in _LABEL_VALUE.finditer(text):
        try: value = float(match.group("value").replace(",", "."))
        except ValueError: continue
        label = re.sub(r"\s+", " ", match.group("label").casefold()).strip()
        unit = match.group("unit").casefold().rstrip(".")
        values.setdefault(label, set()).add((value, unit))
    return values


def _normal_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return re.sub(r"\s+", " ", "".join(ch for ch in decomposed if not unicodedata.combining(ch))).strip()


def family_key(source: Mapping[str, Any]) -> str:
    name = _normal_text(str(source.get("name") or ""))
    name = re.sub(rf"{_NUMBER}\s*(?:{_UNIT})\b", " ", name, flags=re.IGNORECASE)
    name = re.sub(r"\b(?:varios|diferentes|distintos)\s+(?:colores|variantes)\b", " ", name)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", name)).strip()


def _size_tokens(text: str) -> set[str]:
    return {re.sub(r"\s+", "", match.group(1).casefold()) for match in _SIZE.finditer(text)}


def source_consistency_flags(source: Mapping[str, Any]) -> list[str]:
    normalized = source_from_mapping(source)
    flags: set[str] = set()
    spec_values = _measurements(normalized["spec"])
    for kind, values in spec_values.items():
        for other in (_measurements(normalized["details"]), _measurements(normalized["description"])):
            if kind in other and values != other[kind]:
                flags.add("NUMERIC_CONFLICT")
                if {unit for _, unit in values} != {unit for _, unit in other[kind]}: flags.add("UNIT_CONFLICT")
    for label, values in _label_measurements(normalized["details"]).items():
        if len(values) > 1: flags.add("NUMERIC_CONFLICT")
    size_sets = [_size_tokens(normalized[field]) for field in ("spec", "details")]
    if all(size_sets) and size_sets[0] != size_sets[1]: flags.add("SIZE_RANGE_CONFLICT")
    types = {_normal_text(value) for field in ("name", "description", "details") for value in _EXPLICIT_TYPE.findall(normalized[field]) if value.strip()}
    if len(types) > 1: flags.add("PRODUCT_OBJECT_CONFLICT")
    materials = {_normal_text(value) for field in ("spec", "description", "details") for value in _EXPLICIT_MATERIAL.findall(normalized[field]) if value.strip()}
    if len(materials) > 1: flags.add("MATERIAL_CONFLICT")
    for pattern, flag in ((_EXPLICIT_BRAND, "BRAND_CONFLICT"), (_EXPLICIT_MODEL, "MODEL_CONFLICT")):
        values = {_normal_text(value) for field in SOURCE_FIELDS for value in pattern.findall(normalized[field]) if value.strip()}
        if len(values) > 1: flags.add(flag)
    return sorted(flags)


def consistency_status(flags: list[str]) -> str:
    return "SOURCE_CONFLICT_REVIEW" if flags else "CONSISTENT"
