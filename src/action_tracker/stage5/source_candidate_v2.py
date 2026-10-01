"""Contracts and deterministic checks for Stage 5 source-version candidates.

This module is intentionally source-only.  It does not translate, write the
production database, or decide whether a source conflict is true or false.
It provides deterministic provenance and conservative conflict flags so that
human review can make the final Gold decision.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from ..products.details_parser import parse_details
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
_SOURCE_ANOMALY_RULES_PATH = Path(__file__).resolve().parents[3] / "config/stage5/source_anomaly_rules.json"


class SourceConsistencyConfigError(ValueError):
    """The source-anomaly configuration is malformed or cannot be compiled."""

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
_PACKAGED_MEASURE = re.compile(
    r"(?P<count>\d+(?:[.,]\d+)?)\s*[x×]\s*"
    r"(?P<amount>\d+(?:[.,]\d+)?)\s*"
    r"(?P<unit>gramos?|kg|g|mg|litros?|l|ml|cl|unidades?|uds?\.?|piezas?|pares?|hojas?)\b",
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


def _canonical_measure_unit(unit: str) -> str:
    """Normalize inflection and unambiguous spelling aliases, not conversions."""

    unit = unit.casefold().rstrip(".")
    aliases = {
        "unidad": "unidad", "unidades": "unidad", "ud": "unidad", "uds": "unidad",
        "pieza": "unidad", "piezas": "unidad", "par": "par", "pares": "par",
        "hoja": "hoja", "hojas": "hoja",
        "gramo": "g", "gramos": "g", "g": "g",
        "litro": "l", "litros": "l", "l": "l",
        "pulgada": "in", "pulgadas": "in", "in": "in",
        "voltio": "v", "voltios": "v", "v": "v",
        "watio": "w", "watios": "w", "w": "w",
        "día": "día", "días": "día", "mes": "mes", "meses": "mes",
        "año": "año", "años": "año",
    }
    return aliases.get(unit, unit)


def _measurements(text: str) -> dict[str, set[tuple[float, str]]]:
    values: dict[str, set[tuple[float, str]]] = {}
    for match in _MEASUREMENT.finditer(text):
        raw_value = match.group("value").replace(",", ".")
        unit = _canonical_measure_unit(match.group("unit"))
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
        unit = _canonical_measure_unit(match.group("unit"))
        try:
            value = float(raw_value)
        except ValueError:
            continue
        values.setdefault(label, set()).add((value, unit))
    return values


_COMPARABLE_DETAIL_LABELS = {
    "count": re.compile(
        r"\b(?:cantidad|numero de unidades|numero de hojas|unidades|piezas|hojas|contenido del paquete)\b"
    ),
    "mass": re.compile(r"\b(?:contenido|peso|peso neto|gramaje)\b"),
    "volume": re.compile(r"\b(?:contenido|volumen|capacidad)\b"),
    "length": re.compile(
        r"^(?:alto|altura|ancho|anchura|largo|profundidad|diametro|medidas|dimensiones)\b"
        r"|^longitud(?:$|\s*[:：])"
    ),
    "technical": re.compile(r"\b(?:potencia|voltaje|tension|frecuencia|capacidad de la bateria|amperaje)\b"),
    "denier": re.compile(r"\b(?:denier|densidad del tejido)\b"),
}


def _comparable_detail_measurements(text: str) -> dict[str, set[tuple[float, str]]]:
    """Extract detail measurements only from labels semantically comparable to spec.

    Product-detail pages often contain unrelated measurements in the same unit
    (especially nutritional grams, battery values, and dimensions). Comparing
    every number in ``details`` to a package spec creates false source conflicts.
    Unlabeled detail values are intentionally excluded; only explicit quantity/
    property labels can corroborate or contradict the corresponding spec kind.
    """

    values: dict[str, set[tuple[float, str]]] = {}
    for pair in parse_details(text):
        label = _normal_text(pair.key_es)
        for kind, label_pattern in _COMPARABLE_DETAIL_LABELS.items():
            if not label_pattern.search(label):
                continue
            if kind == "length" and re.search(r"\bincl\.?\s*envase\b", label):
                # Packaged dimensions are not directly comparable with the
                # product's unboxed dimensions in its general specification.
                continue
            if kind == "count" and re.fullmatch(r"numero de hojas", label):
                raw_count = pair.value_es.strip().replace(",", ".")
                if re.fullmatch(_NUMBER, raw_count):
                    values.setdefault(kind, set()).add((float(raw_count), "hoja"))
                continue
            for measured_kind, measurements in _measurements(pair.value_es).items():
                if measured_kind == kind:
                    values.setdefault(kind, set()).update(measurements)
    return values


def _comparable_description_measurements(text: str) -> dict[str, set[tuple[float, str]]]:
    """Extract only explicitly labeled measurements from free-text descriptions."""

    values: dict[str, set[tuple[float, str]]] = {}
    for match in _LABEL_VALUE.finditer(text):
        label = _normal_text(match.group("label"))
        unit = _canonical_measure_unit(match.group("unit"))
        label_pattern = _COMPARABLE_DETAIL_LABELS.get(_measurement_kind(unit))
        if label_pattern is None or not label_pattern.search(label):
            continue
        try:
            value = float(match.group("value").replace(",", "."))
        except ValueError:
            continue
        values.setdefault(_measurement_kind(unit), set()).add((value, unit))
    return values


def _packaged_measure_total(
    text: str, kind: str,
) -> tuple[float, float, float, str, float] | None:
    """Return pack count, per-item value, total, unit and rounding bound."""

    for match in _PACKAGED_MEASURE.finditer(text):
        unit = _canonical_measure_unit(match.group("unit"))
        if _measurement_kind(unit) != kind:
            continue
        count_text = match.group("count").replace(",", ".")
        amount_text = match.group("amount").replace(",", ".")
        try:
            count = float(count_text)
            amount = float(amount_text)
        except ValueError:
            continue
        amount_precision = len(amount_text.partition(".")[2])
        rounding_tolerance = count * 0.5 * (10 ** -amount_precision) + 0.5
        return count, amount, count * amount, unit, rounding_tolerance
    return None


def _measurements_match(
    left: set[tuple[float, str]],
    right: set[tuple[float, str]],
    *,
    packaged_total: tuple[float, float, float, str, float] | None = None,
) -> bool:
    candidates = left
    tolerance_values: dict[tuple[float, str], float] = {}
    if packaged_total is not None:
        count, amount, total, unit, tolerance = packaged_total
        candidates = {(amount, unit), (total, unit)}
        tolerance_values[(total, unit)] = tolerance
        if unit == "unidad":
            # A generic count can describe either inner units or the number of
            # packages in an explicit multipack (e.g. 2×500 units).
            candidates.add((count, unit))
    for left_value, left_unit in candidates:
        left_kind, left_base_unit, left_factor = _measurement_conversion(left_unit)
        tolerance = tolerance_values.get((left_value, left_unit), 0.0) * left_factor
        for right_value, right_unit in right:
            right_kind, right_base_unit, right_factor = _measurement_conversion(right_unit)
            if left_kind != right_kind or left_base_unit != right_base_unit:
                continue
            if abs(left_value * left_factor - right_value * right_factor) <= tolerance + 1e-9:
                return True
    return False


def _measurement_conversion(unit: str) -> tuple[str, str, float]:
    """Return a measurement's dimension, comparison unit and deterministic factor."""

    unit = _canonical_measure_unit(unit)
    conversions = {
        "kg": ("mass", "g", 1000.0), "g": ("mass", "g", 1.0),
        "mg": ("mass", "g", 0.001), "mcg": ("mass", "g", 0.000001),
        "µg": ("mass", "g", 0.000001), "ug": ("mass", "g", 0.000001),
        "l": ("volume", "ml", 1000.0), "cl": ("volume", "ml", 10.0),
        "ml": ("volume", "ml", 1.0),
        "m": ("length", "mm", 1000.0), "cm": ("length", "mm", 10.0),
        "mm": ("length", "mm", 1.0), "in": ("length", "mm", 25.4),
    }
    return conversions.get(unit, (_measurement_kind(unit), unit, 1.0))


def _measurement_units_compatible(
    left: set[tuple[float, str]], right: set[tuple[float, str]],
) -> bool:
    return any(
        _measurement_conversion(left_unit)[:2] == _measurement_conversion(right_unit)[:2]
        for _, left_unit in left
        for _, right_unit in right
    )


def _comparison_spec_values(
    spec_values: set[tuple[float, str]],
    packaged_total: tuple[float, float, float, str, float] | None,
) -> set[tuple[float, str]]:
    if packaged_total is None:
        return spec_values
    count, amount, total, unit, _ = packaged_total
    candidates = {(amount, unit), (total, unit)}
    if unit == "unidad":
        candidates.add((count, unit))
    return candidates


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


def _configured_detail_anomaly_evidence(
    details: str,
    rules: tuple[tuple[re.Pattern[str], re.Pattern[str] | None, str], ...],
) -> list[dict[str, Any]]:
    """Match configured anomalies in colon and legacy flattened key/value rows."""
    evidence: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for pair in parse_details(details):
        candidates = [(pair.key_es, pair.value_es, "configured source detail key/value rule matched")]
        if not pair.value_es.strip():
            words = pair.key_es.split()
            # Legacy Action pages sometimes flatten ``Key Value`` into one
            # segment with no colon.  Try every non-empty prefix/suffix split;
            # a configured anchored key regex keeps this conservative.
            candidates.extend(
                (" ".join(words[:index]), " ".join(words[index:]),
                 "configured source detail legacy flattened key/value rule matched")
                for index in range(1, len(words))
            )
        for key_text, value_text, reason in candidates:
            key = _normal_text(key_text)
            value = _normal_text(value_text)
            for key_pattern, value_pattern, flag in rules:
                identity = (flag, pair.position)
                if identity in seen:
                    continue
                if key_pattern.search(key) and (value_pattern is None or value_pattern.search(value)):
                    seen.add(identity)
                    evidence.append({
                        "code": flag,
                        "source_field": "details",
                        "source_key": key_text,
                        "source_value": value_text,
                        "source_pair": pair.raw,
                        "reason": reason,
                    })
    return evidence


def source_consistency_flags(source: Mapping[str, Any]) -> list[str]:
    """Conservatively flag explicit cross-field contradictions.

    A flag is a review signal, never an automatic rewrite.  The checks focus
    on facts that can be compared mechanically; absence of a flag is not a
    semantic translation approval.
    """

    normalized = source_from_mapping(source)
    flags: set[str] = set()
    _, rules = _source_anomaly_rules()
    flags.update(
        str(item["code"])
        for item in _configured_detail_anomaly_evidence(normalized["details"], rules)
    )
    spec_measurements = _measurements(normalized["spec"])
    details_measurements = _comparable_detail_measurements(normalized["details"])
    description_measurements = _comparable_description_measurements(normalized["description"])
    for kind, spec_values in spec_measurements.items():
        for other in (details_measurements, description_measurements):
            if kind not in other:
                continue
            packaged_total = _packaged_measure_total(normalized["spec"], kind)
            comparison_values = _comparison_spec_values(spec_values, packaged_total)
            if not _measurements_match(
                comparison_values, other[kind], packaged_total=packaged_total
            ):
                flags.add("NUMERIC_CONFLICT")
                if not _measurement_units_compatible(comparison_values, other[kind]):
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


def source_consistency_evidence(source: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return field-level evidence behind configured anomalies/conflict flags."""
    normalized = source_from_mapping(source)
    _, rules = _source_anomaly_rules()
    evidence: list[dict[str, Any]] = []
    configured_evidence = _configured_detail_anomaly_evidence(normalized["details"], rules)
    evidence.extend(configured_evidence)
    detail_flags = {str(item["code"]) for item in configured_evidence}

    fields_by_flag = {
        "NUMERIC_CONFLICT": ("spec", "details", "description"),
        "UNIT_CONFLICT": ("spec", "details", "description"),
        "SIZE_RANGE_CONFLICT": ("spec", "details"),
        "PRODUCT_OBJECT_CONFLICT": ("name", "description", "details"),
        "MATERIAL_CONFLICT": ("spec", "description", "details"),
        "BRAND_CONFLICT": SOURCE_FIELDS,
        "MODEL_CONFLICT": SOURCE_FIELDS,
    }
    for flag in source_consistency_flags(normalized):
        if flag in detail_flags:
            continue
        affected_fields = fields_by_flag.get(flag, SOURCE_FIELDS)
        row: dict[str, Any] = {
            "code": flag,
            "source_field": "multiple",
            "source_fields": {field: normalized[field] for field in affected_fields},
            "reason": "cross-field consistency rule matched",
        }
        if flag in {"NUMERIC_CONFLICT", "UNIT_CONFLICT"}:
            measurements = {
                "spec": _measurements(normalized["spec"]),
                "details": _measurements(normalized["details"]),
                "description": _measurements(normalized["description"]),
            }
            row["parsed_measurements"] = {
                field: {
                    kind: [
                        {"value": value, "unit": unit}
                        for value, unit in sorted(values)
                    ]
                    for kind, values in sorted(by_kind.items())
                }
                for field, by_kind in measurements.items()
            }
            comparisons = []
            spec_values = measurements["spec"]
            for other_field in ("details", "description"):
                other_values = (
                    _comparable_detail_measurements(normalized["details"])
                    if other_field == "details"
                    else _comparable_description_measurements(normalized["description"])
                )
                for kind in sorted(spec_values.keys() & other_values.keys()):
                    right = other_values[kind]
                    packaged_total = _packaged_measure_total(normalized["spec"], kind)
                    left = _comparison_spec_values(spec_values[kind], packaged_total)
                    if _measurements_match(left, right, packaged_total=packaged_total):
                        continue
                    comparison = {
                        "kind": kind,
                        "left_field": "spec",
                        "left_values": [
                            {"value": value, "unit": unit}
                            for value, unit in sorted(left)
                        ],
                        "right_field": other_field,
                        "right_values": [
                            {"value": value, "unit": unit}
                            for value, unit in sorted(right)
                        ],
                        "numeric_values_differ": not _measurements_match(
                            left, right, packaged_total=packaged_total
                        ),
                        "units_differ": {unit for _, unit in left} != {unit for _, unit in right},
                        "units_incompatible": not _measurement_units_compatible(left, right),
                    }
                    if packaged_total is not None:
                        comparison["left_basis"] = "package_count_or_item_amount_or_package_total"
                        comparison["package_count"] = packaged_total[0]
                        comparison["rounding_tolerance"] = packaged_total[4]
                    comparisons.append(comparison)
            row["cross_field_comparisons"] = comparisons
            row["detail_comparison_policy"] = "explicit_semantically_comparable_labels_only"
            row["conflicting_labeled_details"] = {
                label: [
                    {"value": value, "unit": unit}
                    for value, unit in sorted(values)
                ]
                for label, values in sorted(_label_measurements(normalized["details"]).items())
                if len(values) > 1
            }
        evidence.append(row)
    return sorted(evidence, key=lambda item: (item["code"], item.get("source_key", ""), item.get("source_pair", "")))


@lru_cache(maxsize=1)
def _source_anomaly_rules() -> tuple[str, tuple[tuple[re.Pattern[str], re.Pattern[str] | None, str], ...]]:
    try:
        payload = json.loads(_SOURCE_ANOMALY_RULES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceConsistencyConfigError("SOURCE_ANOMALY_CONFIG_READ_FAILED") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("version"), str) or not payload["version"].strip():
        raise SourceConsistencyConfigError("SOURCE_ANOMALY_CONFIG_VERSION_MISSING")
    rows = payload.get("detail_pair_rules")
    if not isinstance(rows, list) or not rows:
        raise SourceConsistencyConfigError("SOURCE_ANOMALY_RULE_LIST_INVALID")
    compiled = []
    seen_flags: set[str] = set()
    try:
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise SourceConsistencyConfigError(f"SOURCE_ANOMALY_RULE_INVALID:{index}")
            key_regex = row.get("key_regex")
            value_regex = row.get("value_regex")
            flag = row.get("flag")
            if (
                not isinstance(key_regex, str) or not key_regex.strip()
                or (value_regex is not None and (not isinstance(value_regex, str) or not value_regex.strip()))
                or not isinstance(flag, str) or not flag.startswith("SOURCE_ANOMALY_")
                or flag in seen_flags
            ):
                raise SourceConsistencyConfigError(f"SOURCE_ANOMALY_RULE_INVALID:{index}")
            seen_flags.add(flag)
            compiled.append((
                re.compile(key_regex, re.IGNORECASE),
                re.compile(value_regex, re.IGNORECASE) if value_regex else None,
                flag,
            ))
    except re.error as exc:
        raise SourceConsistencyConfigError("SOURCE_ANOMALY_REGEX_INVALID") from exc
    return payload["version"].strip(), tuple(compiled)


def source_consistency_rules_manifest() -> dict[str, str]:
    """Hash both declarative source rules and the implementation that applies them."""
    version, _ = _source_anomaly_rules()
    config_sha256 = hashlib.sha256(_SOURCE_ANOMALY_RULES_PATH.read_bytes()).hexdigest()
    implementation_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    combined_sha256 = hashlib.sha256(
        f"{version}:{config_sha256}:{implementation_sha256}".encode("utf-8")
    ).hexdigest()
    return {
        "version": version,
        "sha256": combined_sha256,
        "config_sha256": config_sha256,
        "implementation_sha256": implementation_sha256,
    }


def consistency_status(flags: list[str]) -> str:
    return "SOURCE_CONFLICT_REVIEW" if flags else "CONSISTENT"
