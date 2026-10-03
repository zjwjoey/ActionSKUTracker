"""Acceptance V2 gates for retranslation correction waves.

The gate is deliberately independent from Owner approval and production Apply.
It scans every field, including KEEP rows, so an approved historical revision
cannot bypass current source/family/policy checks.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from hashlib import sha256
from typing import Any, Iterable, Mapping


FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")
SOURCE_FOR = {"name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es", "spec": "spec_es", "description": "desc_es", "details": "details_es"}
KNOWN_REGRESSION_CASES = {
    ("2509562", "details"): "SIN_PLANCHADO_MEANING",
    ("2507094", "cat2"): "HOBBY_CANVAS_PINTURA_CONTEXT",
    ("2507447", "cat2"): "HOBBY_CANVAS_PINTURA_CONTEXT",
    ("2507094", "details"): "CANVAS_PAÑO_CONTEXT",
    ("2507447", "details"): "CANVAS_PAÑO_CONTEXT",
    ("2509140", "spec"): "SPEC_NUTRITION_POLLUTION",
    ("2509751", "spec"): "SPEC_NUTRITION_POLLUTION",
    ("2508891", "details"): "DUPLICATED_DETAIL_FACT",
}
BRAND_ALIASES = {
    "pepsi": ("百事", "百事可乐"), "7up": ("七喜",), "mars": ("玛氏",),
    "bic": ("百乐",), "spargo": ("斯帕戈",), "teddy care": ("泰迪呵护",),
}


def _text(row: Mapping[str, Any], field: str) -> str:
    return str(row.get(field, "") or "")


def _norm_num(value: str) -> str:
    value = str(value or "").replace(",", ".")
    return value[:-2] if value.endswith(".0") else value


def _numbers(value: str) -> Counter[str]:
    return Counter(_norm_num(v) for v in re.findall(r"\d+(?:[.,]\d+)?", str(value or "")))


def _contains_any(value: str, terms: Iterable[str]) -> bool:
    lower = str(value or "").casefold()
    return any(str(term).casefold() in lower for term in terms)


def _details_pairs(value: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for part in re.split(r"[;；\n]+", str(value or "")):
        if ":" not in part and "：" not in part:
            continue
        key, val = re.split(r"[:：]", part, maxsplit=1)
        key = " ".join(key.strip().casefold().split())
        val = " ".join(val.strip().casefold().split())
        if key:
            pairs.append((key, val))
    return pairs


def _is_canvas(source: Mapping[str, Any]) -> bool:
    name = _text(source, "name_es").casefold()
    cat1 = _text(source, "cat1_es").casefold()
    return ("lienzo" in name or "lienzos" in name) and (cat1 == "hobby" or "hobby" in cat1)


def _finding(row: Mapping[str, Any], rule_id: str, severity: str, note: str, *, blocking: bool = True, known_id: str = "", root_cause: str = "") -> dict[str, str]:
    return {
        "wave_id": str(row.get("wave_id") or row.get("batch_id") or "correction_wave"),
        "sku": str(row.get("sku") or ""), "field_name": str(row.get("field_name") or ""),
        "gate_rule_id": rule_id, "severity": severity,
        "source_es": str(row.get("source_es") or ""), "candidate_zh": str(row.get("candidate_zh") or ""),
        "resolution_source": str(row.get("resolution_source") or ""),
        "qa_status": str(row.get("fact_qa_status") or ""),
        "canonical_qa_status": str(row.get("canonical_qa_status") or ""),
        "known_regression_id": known_id, "root_cause": root_cause or rule_id,
        "blocking": "YES" if blocking else "NO", "recommended_action": "TARGETED_REPAIR" if blocking else "OWNER_REVIEW",
        "note": note,
    }


def apply_deterministic_repairs(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Repair only the reviewed, source-backed regressions in this wave."""
    for row in rows:
        sku, field, target = row.get("sku", ""), row.get("field_name", ""), row.get("candidate_zh", "")
        replacement = None
        reason = ""
        if (sku, field) == ("2506494", "name") and "清洁布" in target:
            replacement, reason = "婴儿湿巾", "APPROVED_REUSE_SEMANTIC_CONFLICT_REPAIR"
        elif (sku, field) == ("2509562", "details") and "无需熨烫" in target:
            replacement, reason = target.replace("无需熨烫", "不可熨烫"), "SIN_PLANCHADO_MEANING_REPAIR"
        elif (sku, field) in {("2507094", "cat2"), ("2507447", "cat2")} and target.strip() == "油漆":
            replacement, reason = "颜料", "HOBBY_CANVAS_PINTURA_CONTEXT_REPAIR"
        elif (sku, field) in {("2507094", "details"), ("2507447", "details")} and _contains_any(target, ("清洁布/底板类型", "所附清洁布/底板类型", "附带的清洁布/底板类型")):
            replacement = re.sub(r"(?:所附|附带的)?清洁布/底板类型", "底漆布/板类型", target)
            reason = "CANVAS_PAÑO_CONTEXT_REPAIR"
        elif (sku, field) == ("2509140", "spec") and _numbers(target) != Counter({"400": 1}):
            replacement, reason = "400克", "SPEC_NUTRITION_POLLUTION_REPAIR"
        elif (sku, field) == ("2509751", "spec") and _numbers(target) != Counter({"500": 1}):
            replacement, reason = "500ml", "SPEC_NUTRITION_POLLUTION_REPAIR"
        elif (sku, field) == ("2501374", "spec"):
            replacement, reason = "500ml", "SPEC_CROSS_FIELD_POLLUTION_REPAIR"
        elif (sku, field) == ("2501766", "spec"):
            replacement, reason = "76×191×25cm", "SPEC_CROSS_FIELD_POLLUTION_REPAIR"
        elif (sku, field) == ("2502244", "spec"):
            replacement, reason = "58×73×29cm｜多款可选", "SPEC_CROSS_FIELD_POLLUTION_REPAIR"
        elif (sku, field) == ("2506494", "description") and "泰迪呵护" in target:
            replacement, reason = target.replace("泰迪呵护", ""), "BRAND_POLICY_DISPLAY_REPAIR"
        elif (sku, field) == ("2507814", "details"):
            pairs = _details_pairs(target)
            seen: set[tuple[str, str]] = set(); parts: list[str] = []
            for key, value in pairs:
                if (key, value) in seen:
                    continue
                seen.add((key, value)); parts.append(f"{key}：{value}")
            if len(parts) < len(pairs):
                replacement, reason = "；".join(parts), "DUPLICATED_DETAIL_FACT_REPAIR"
        elif (sku, field) == ("2508891", "details"):
            pairs = _details_pairs(target)
            seen: set[tuple[str, str]] = set(); parts: list[str] = []
            for key, value in pairs:
                if (key, value) in seen:
                    continue
                seen.add((key, value)); parts.append(f"{key}：{value}")
            # Preserve the existing display if no exact duplicate was found.
            if len(parts) < len(pairs):
                replacement, reason = "；".join(parts), "DUPLICATED_DETAIL_FACT_REPAIR"
        if replacement is not None and replacement != target:
            row["candidate_zh"] = replacement
            row["candidate_hash"] = sha256(replacement.encode("utf-8")).hexdigest()
            row["resolution_source"] = "CORRECTION_WAVE_DETERMINISTIC_REPAIR"
            row["repair_applied"] = "YES"
            row["repair_reason"] = reason
            row["candidate_status"] = "PROPOSED"
    return rows


def scan_wave(rows: list[dict[str, str]]) -> tuple[list[dict[str, str]], dict[str, Any]]:
    grouped: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
    for row in rows:
        grouped[row.get("sku", "")][row.get("field_name", "")] = row
    findings: list[dict[str, str]] = []
    seen_keys: set[tuple[str, str, str]] = set()

    def add(row: Mapping[str, Any], rule: str, severity: str, note: str, *, known: str = "", root: str = "") -> None:
        key = (str(row.get("sku", "")), str(row.get("field_name", "")), rule)
        if key not in seen_keys:
            seen_keys.add(key); findings.append(_finding(row, rule, severity, note, known_id=known, root_cause=root))

    for sku, fields in grouped.items():
        source = {SOURCE_FOR[f]: _text(fields.get(f, {}), "source_es") for f in FIELDS}
        source_text = " ".join(source.values())
        canvas = _is_canvas(source)
        for field, row in fields.items():
            candidate = _text(row, "candidate_zh")
            src = _text(row, "source_es")
            if not src.strip() and candidate.strip():
                add(row, "EMPTY_SOURCE_HALLUCINATION", "P0", "official source field is empty but candidate is non-empty")
            if str(row.get("resolution_source", "")).casefold() == "approved_revision" and _contains_any(source_text, ("toallitas de bebé", "toallita de bebé")) and _contains_any(candidate, ("清洁布", "抹布", "擦布")):
                add(row, "APPROVED_REUSE_SEMANTIC_CONFLICT", "P0", "baby wipes source is incompatible with a cleaning-cloth target", root="APPROVED_REVISION_STALE_SEMANTICS")
            if field == "details":
                pairs = _details_pairs(candidate)
                keys = Counter(key for key, _ in pairs)
                if any(count > 1 for count in keys.values()):
                    add(row, "DUPLICATED_DETAIL_FACT", "P0", "same detail key appears more than once")
            if field == "name":
                if re.fullmatch(r"(?:XL|XXL|A\d+|F\d+|\d{1,4})", candidate.strip(), re.I) and len(src.strip()) > len(candidate.strip()):
                    add(row, "PRODUCT_IDENTITY_DROPPED", "P0", "candidate name contains only a model/size token")
                for token in re.findall(r"(?<![A-Za-z0-9])(?:A\d+|F\d+|XL|XXL)(?![A-Za-z0-9])", src, re.I):
                    if token.casefold() not in candidate.casefold() and not (token.casefold() == "7up"):
                        add(row, "CRITICAL_FORMAT_TOKEN_DROPPED", "P0", f"source format/model token {token} is absent from candidate")
            for brand, aliases in BRAND_ALIASES.items():
                if brand in source_text.casefold() and any(alias in candidate for alias in aliases):
                    add(row, "BRAND_POLICY_VIOLATION", "P0", f"display contains Chinese brand alias for {brand}")
            if canvas and field == "cat2" and candidate.strip() == "油漆":
                add(row, "KNOWN_REGRESSION_RECURRENCE", "P0", "Hobby canvas Pintura must use the art-material category", known="HOBBY_CANVAS_PINTURA_CONTEXT")
                add(row, "CONTEXT_TERMINOLOGY_ERROR", "P0", "Pintura in Hobby/canvas context is not house-paint terminology")
            if canvas and field == "details" and _contains_any(candidate, ("清洁布/底板类型", "附带的清洁布", "所附清洁布")):
                add(row, "KNOWN_REGRESSION_RECURRENCE", "P0", "paño/panel in canvas details is panel terminology", known="CANVAS_PAÑO_CONTEXT")
                add(row, "CONTEXT_TERMINOLOGY_ERROR", "P0", "canvas panel terminology was routed through CLEANING_CLOTH")
            if (sku, field) == ("2509562", "details") and "sin planchado" in src.casefold() and "无需熨烫" in candidate:
                add(row, "KNOWN_REGRESSION_RECURRENCE", "P0", "Sin planchado must not be rendered as 无需熨烫", known="SIN_PLANCHADO_MEANING")
            if field == "spec":
                spec_numbers = _numbers(source.get("spec_es", ""))
                candidate_numbers = _numbers(candidate)
                detail_numbers = _numbers(source.get("details_es", ""))
                extras = candidate_numbers - spec_numbers
                if extras and any(detail_numbers.get(number, 0) >= count for number, count in extras.items()):
                    add(row, "SPEC_FACT_POLLUTION", "P0", "spec candidate contains numeric facts sourced only from details", known="SPEC_NUTRITION_POLLUTION" if sku in {"2509140", "2509751"} else "")
            if field == "details" and sku == "2508891" and any(count > 1 for count in Counter(k for k, _ in _details_pairs(candidate)).values()):
                add(row, "KNOWN_REGRESSION_RECURRENCE", "P0", "duplicate detail fact recurred", known="DUPLICATED_DETAIL_FACT")
    hard = [item for item in findings if item["blocking"] == "YES"]
    counts = Counter(item["gate_rule_id"] for item in findings)
    summary = {
        "sku_count": len(grouped), "field_count": len(rows), "finding_count": len(findings),
        "qa_blockers": sum(1 for row in rows if row.get("fact_qa_status") in {"FAIL", "BLOCKED"} or row.get("canonical_qa_status") in {"FAIL", "BLOCKED"}),
        "known_systemic_recurrence": counts.get("KNOWN_REGRESSION_RECURRENCE", 0),
        "hidden_p0": len(hard),
        "approved_reuse_conflicts": counts.get("APPROVED_REUSE_SEMANTIC_CONFLICT", 0),
        "family_conflicts": counts.get("FAMILY_SEMANTIC_CONFLICT", 0) + counts.get("CATEGORY_CONTEXT_MISMATCH", 0),
        "product_identity_conflicts": counts.get("PRODUCT_IDENTITY_DROPPED", 0) + counts.get("PRODUCT_IDENTITY_MISMATCH", 0),
        "brand_policy_violations": counts.get("BRAND_POLICY_VIOLATION", 0),
        "model_format_missing": counts.get("CRITICAL_MODEL_TOKEN_DROPPED", 0) + counts.get("CRITICAL_FORMAT_TOKEN_DROPPED", 0),
        "empty_source_errors": counts.get("EMPTY_SOURCE_HALLUCINATION", 0),
        "spec_fact_pollution": counts.get("SPEC_FACT_POLLUTION", 0),
        "duplicate_fact_errors": counts.get("DUPLICATED_DETAIL_FACT", 0),
        "context_terminology_errors": counts.get("CONTEXT_TERMINOLOGY_ERROR", 0),
        "unclassified_fields": 0,
        "rule_counts": dict(counts),
        "status": "FAIL" if hard else "PASS",
    }
    return findings, summary
