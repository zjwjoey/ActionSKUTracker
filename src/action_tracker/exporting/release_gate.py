"""Hard release gate for SQLite-backed ES/ZH exports.

The gate is intentionally independent from workbook formatting.  It compares
the exported projection with the same source records and checks field-level
localization provenance before a PRIMARY database export is published.
"""
from __future__ import annotations

import math
import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from ..services.hashing import localization_field_source_hash
from ..translation.detail_terminology import (
    DetailTerminologyConfigError, load_detail_terminology_rules, repair_detail_candidate,
)
from ..translation.description_fidelity import (
    DescriptionFidelityPolicyError, description_compression_finding, load_description_fidelity_policy,
)
from ..translation.model_guard import validate_model_output
from ..translation.approved_terms import (
    inspect_approved_term_candidate, inspect_approved_term_cross_field_movement,
)
from ..translation.source_fact_repair import repair_model_output as inspect_source_facts
from ..translation.title_policy import has_unresolved_chinese_brand_marker, load_title_display_policy
from ..localization.gold_gate import evaluate_gold_gate
from ..stage5.source_candidate_v2 import source_consistency_evidence, source_consistency_rules_manifest

_DETAIL_RULES_PATH = Path(__file__).resolve().parents[3] / "config/stage5/detail_terminology_rules.json"
_DESCRIPTION_POLICY_PATH = Path(__file__).resolve().parents[3] / "config/stage5/description_fidelity_policy.json"
_TITLE_POLICY_PATH = Path(__file__).resolve().parents[3] / "config/stage5/title_display_policy.json"
_APPROVED_TERM_CHECKER_PATH = Path(__file__).resolve().parents[3] / "src/action_tracker/translation/approved_terms.py"
_ZH_GUARD_CODES = frozenset({
    "NUMERIC_DROPPED", "NUMERIC_HALLUCINATED", "UNIT_DROPPED", "UNIT_HALLUCINATED",
    "TECH_TOKEN_DROPPED", "TECH_TOKEN_HALLUCINATED", "CERTIFICATION_DROPPED",
    "CERTIFICATION_HALLUCINATED", "INTERNAL_QA_NOTE_LEAKED",
    "NEGATION_DROPPED", "NEGATION_HALLUCINATED", "UNSUPPORTED_NEGATIVE_ATTRIBUTE",
    "SOURCE_EMPTY_NONEMPTY", "EMPTY_REQUIRED_FIELD", "INVALID_CATEGORY",
    "SPANISH_RESIDUAL", "ENGLISH_RESIDUAL", "BRAND_RETAINED",
    "SOURCE_FACT_REVIEW_REQUIRED",
})


APPROVED_ZH_STATUSES = frozenset({
    "APPROVED", "HUMAN_APPROVED", "CONFIRMED", "LOCKED", "HUMAN_REVIEWED",
})
CURRENT_ZH_FRESHNESS_STATUSES = frozenset({"CURRENT", "FRESH"})
_HTML_RE = re.compile(r"<\/?[a-z][^>]*>", re.IGNORECASE)


class ReleaseGateError(ValueError):
    """A formal release has one or more blocking gate failures."""


def evaluate_release_gate(
    records: Iterable[dict[str, Any]],
    rows: Iterable[dict[str, Any]],
    *,
    language: str,
    strict: bool,
    explicit_exceptions: Iterable[dict[str, Any]] = (),
    confirmed_brand_phrases_by_sku: dict[str, tuple[str, ...]] | None = None,
    approved_terms: Iterable[dict[str, str]] = (),
    source_snapshot_frozen: bool = False,
    provenance_complete: bool = False,
) -> dict[str, Any]:
    """Return machine-readable gate counters; optionally raise on any failure."""
    approved_terms = tuple(approved_terms)
    source_by_sku = {str(row.get("sku") or "").strip(): row for row in records}
    output_by_sku = {str(row.get("编号") or "").strip(): row for row in rows}
    issues: list[dict[str, Any]] = []
    try:
        detail_terminology_rules = load_detail_terminology_rules(_DETAIL_RULES_PATH) if language == "zh" else None
    except DetailTerminologyConfigError as exc:
        raise ReleaseGateError(f"DETAIL_TERMINOLOGY_CONFIG_INVALID:{exc}") from exc
    try:
        description_policy = load_description_fidelity_policy(_DESCRIPTION_POLICY_PATH) if language == "zh" else None
    except DescriptionFidelityPolicyError as exc:
        raise ReleaseGateError(f"DESCRIPTION_FIDELITY_POLICY_INVALID:{exc}") from exc
    title_display_policy = load_title_display_policy(_TITLE_POLICY_PATH) if language == "zh" else None
    _issue_if(issues, "SKU_SET_MISMATCH", set(source_by_sku) != set(output_by_sku),
              expected=len(source_by_sku), actual=len(output_by_sku))

    for sku, source in source_by_sku.items():
        out = output_by_sku.get(sku)
        if out is None:
            continue
        _compare_shared_fact(issues, sku, source, out)
        if language == "es":
            _check_spanish_fields(issues, sku, out)
        elif language == "zh":
            _check_source_anomalies(issues, sku, source)
            phrases = (confirmed_brand_phrases_by_sku or {}).get(sku, ())
            _check_zh_provenance(
                issues, sku, source, out, detail_terminology_rules or {},
                allowed_brand_phrases=phrases,
                no_brand_policy=title_display_policy is not None,
                description_policy=description_policy,
                approved_terms=approved_terms,
            )
            _check_zh_official_labels(issues, sku, source.get("raw_tags"), out.get("备注"))

    exceptions = {
        (str(item.get("sku") or ""), str(item.get("field") or ""), str(item.get("code") or ""))
        for item in explicit_exceptions
        if str(item.get("status") or "") == "EXPLICIT_EXCEPTION"
        and not (str(item.get("field") or "") == "name" and str(item.get("code") or "") == "BRAND_RETAINED")
    }
    remaining = [item for item in issues if (str(item.get("sku") or ""), str(item.get("field") or ""), str(item.get("code") or "")) not in exceptions]
    blocking = [item for item in remaining if not item.get("review_only")]
    review_findings = [item for item in remaining if item.get("review_only")]
    counts = Counter(str(item["code"]) for item in remaining)
    blocking_counts = Counter(str(item["code"]) for item in blocking)
    quality_status = (
        "BLOCKED" if blocking else
        "RELEASE_PASS_WITH_REVIEW_NOT_GOLD" if review_findings else
        "RELEASE_PASS_NOT_GOLD"
    )
    result = {
        "strict": bool(strict),
        "passed": not blocking,
        "quality_status": quality_status,
        "gold_status": "NOT_CERTIFIED",
        "gold_eligible": False,
        "gold_limitation": "EXPORT_GATE_DOES_NOT_EVALUATE_ALL_SIX_GOLD_QA_LAYERS",
        "issues": remaining,
        "review_findings": review_findings,
        "blocking_findings": blocking,
        "explicit_exception_count": len(issues) - len(remaining),
        "title_display_policy": ({
            "policy_id": title_display_policy.get("policy_id"),
            "sha256": hashlib.sha256(_TITLE_POLICY_PATH.read_bytes()).hexdigest(),
        } if title_display_policy else None),
        "detail_terminology_rules": ({
            "version": detail_terminology_rules.get("version"),
            "sha256": hashlib.sha256(_DETAIL_RULES_PATH.read_bytes()).hexdigest(),
        } if detail_terminology_rules else None),
        "description_fidelity_policy": ({
            "policy_id": description_policy.get("policy_id"),
            "sha256": hashlib.sha256(_DESCRIPTION_POLICY_PATH.read_bytes()).hexdigest(),
        } if description_policy else None),
        "source_anomaly_rules": source_consistency_rules_manifest(),
        "approved_term_checker_sha256": hashlib.sha256(_APPROVED_TERM_CHECKER_PATH.read_bytes()).hexdigest()
        if language == "zh" else None,
        "counts": {
            "SKU_SET_MISMATCH": counts.get("SKU_SET_MISMATCH", 0),
            "FACT_MISMATCH": counts.get("FACT_MISMATCH", 0),
            "UNDECLARED_DISPLAY_MISMATCH": counts.get("UNDECLARED_DISPLAY_MISMATCH", 0),
            "UNAPPROVED_ZH": counts.get("UNAPPROVED_ZH", 0),
            "APPROVAL_PROVENANCE_MISSING": counts.get("APPROVAL_PROVENANCE_MISSING", 0),
            "ZH_FRESHNESS_INVALID": counts.get("ZH_FRESHNESS_INVALID", 0),
            "STALE_ZH": counts.get("STALE_ZH", 0),
            "SPANISH_RESIDUAL": counts.get("SPANISH_RESIDUAL", 0),
            "EMPTY_REQUIRED_FIELD": counts.get("EMPTY_REQUIRED_FIELD", 0),
            "SOURCE_EMPTY_NONEMPTY": counts.get("SOURCE_EMPTY_NONEMPTY", 0),
            "SOURCE_HASH_MISMATCH": counts.get("SOURCE_HASH_MISMATCH", 0),
            "NUMERIC_DROPPED": counts.get("NUMERIC_DROPPED", 0),
            "NUMERIC_HALLUCINATED": counts.get("NUMERIC_HALLUCINATED", 0),
            "UNIT_DROPPED": counts.get("UNIT_DROPPED", 0),
            "UNIT_HALLUCINATED": counts.get("UNIT_HALLUCINATED", 0),
            "TECH_TOKEN_DROPPED": counts.get("TECH_TOKEN_DROPPED", 0),
            "TECH_TOKEN_HALLUCINATED": counts.get("TECH_TOKEN_HALLUCINATED", 0),
            "CERTIFICATION_DROPPED": counts.get("CERTIFICATION_DROPPED", 0),
            "CERTIFICATION_HALLUCINATED": counts.get("CERTIFICATION_HALLUCINATED", 0),
            "INTERNAL_QA_NOTE_LEAKED": counts.get("INTERNAL_QA_NOTE_LEAKED", 0),
            "NEGATION_DROPPED": counts.get("NEGATION_DROPPED", 0),
            "NEGATION_HALLUCINATED": counts.get("NEGATION_HALLUCINATED", 0),
            "UNSUPPORTED_NEGATIVE_ATTRIBUTE": counts.get("UNSUPPORTED_NEGATIVE_ATTRIBUTE", 0),
            "INVALID_CATEGORY": counts.get("INVALID_CATEGORY", 0),
            "ENGLISH_RESIDUAL": counts.get("ENGLISH_RESIDUAL", 0),
            "BRAND_RETAINED": counts.get("BRAND_RETAINED", 0),
            "UNRESOLVED_CHINESE_BRAND_MARKER": counts.get("UNRESOLVED_CHINESE_BRAND_MARKER", 0),
            "TERM_FORBIDDEN_TRANSLATION": counts.get("TERM_FORBIDDEN_TRANSLATION", 0),
            "APPROVED_TERM_CANONICAL_ABSENT_REVIEW": counts.get("APPROVED_TERM_CANONICAL_ABSENT_REVIEW", 0),
            "APPROVED_TERM_CROSS_FIELD_REVIEW": counts.get("APPROVED_TERM_CROSS_FIELD_REVIEW", 0),
            "APPROVED_TERM_UNSUPPORTED_TARGET_REVIEW": counts.get("APPROVED_TERM_UNSUPPORTED_TARGET_REVIEW", 0),
            "OFFICIAL_LABEL_DROPPED": counts.get("OFFICIAL_LABEL_DROPPED", 0),
            "OFFICIAL_LABEL_HALLUCINATED": counts.get("OFFICIAL_LABEL_HALLUCINATED", 0),
            "OFFICIAL_LABEL_MISMATCH": counts.get("OFFICIAL_LABEL_MISMATCH", 0),
            "DETAIL_TERMINOLOGY_MISMATCH": counts.get("DETAIL_TERMINOLOGY_MISMATCH", 0),
            "DETAIL_TERMINOLOGY_REVIEW": counts.get("DETAIL_TERMINOLOGY_REVIEW", 0),
            "SOURCE_FACT_REVIEW_REQUIRED": counts.get("SOURCE_FACT_REVIEW_REQUIRED", 0),
            "DESCRIPTION_COMPRESSION_REVIEW": counts.get("DESCRIPTION_COMPRESSION_REVIEW", 0),
            "SOURCE_ANOMALY_REVIEW": counts.get("SOURCE_ANOMALY_REVIEW", 0),
        },
    }
    semantic_prefixes = (
        "DETAIL_", "SOURCE_KEY_TRANSLATION_", "SOURCE_VALUE_TRANSLATION_",
        "SOURCE_ANOMALY_", "APPROVED_TERM_", "DESCRIPTION_COMPRESSION_REVIEW",
        "SOURCE_FACT_REVIEW_REQUIRED",
    )
    semantic_findings = [
        item for item in remaining
        if str(item.get("code") or "").startswith(semantic_prefixes)
    ]
    result["semantic_gate"] = {
        "status": "PASS" if not semantic_findings else "REVIEW_REQUIRED",
        "review_finding_count": len(semantic_findings),
        "unresolved": bool(semantic_findings),
        "gold_blocked": bool(semantic_findings),
        "source_anomaly_rules": source_consistency_rules_manifest(),
        "review_only": True,
    }
    gold_policy_path = Path(__file__).resolve().parents[3] / "config/stage5/chinese_gold_qa_policy.json"
    try:
        gold_policy = json.loads(gold_policy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseGateError(f"GOLD_POLICY_READ_FAILED:{gold_policy_path}") from exc
    result["gold_gate"] = evaluate_gold_gate(
        result, gold_policy,
        source_snapshot_frozen=source_snapshot_frozen,
        provenance_complete=provenance_complete,
    )
    if strict and blocking:
        codes = ",".join(f"{code}={count}" for code, count in sorted(blocking_counts.items()))
        raise ReleaseGateError(f"EXPORT_RELEASE_GATE_BLOCKED:{codes}")
    return result


def _compare_shared_fact(issues: list[dict[str, Any]], sku: str, source: dict[str, Any], out: dict[str, Any]) -> None:
    checks = (
        ("折后价", source.get("current_price"), out.get("折后价"), "FACT_MISMATCH"),
        ("图片链接", _text(source.get("image_url")), _text(out.get("图片链接")), "FACT_MISMATCH"),
        ("商品链接", _text(source.get("product_url")), _text(out.get("商品链接")), "FACT_MISMATCH"),
    )
    for field, expected, actual, code in checks:
        if expected != actual:
            _issue(issues, code, sku, field, expected=expected, actual=actual)
    original = _number(source.get("original_price"))
    expected_original = original if original is not None and original > (_number(source.get("current_price")) or 0) else None
    actual_original = _number(out.get("原价"))
    if expected_original != actual_original:
        _issue(issues, "UNDECLARED_DISPLAY_MISMATCH", sku, "原价", expected=expected_original, actual=actual_original)


def _check_spanish_fields(issues: list[dict[str, Any]], sku: str, out: dict[str, Any]) -> None:
    for field in ("标题", "分类1", "分类2", "规格", "单价", "描述", "产品详情", "备注"):
        value = _text(out.get(field))
        if not value:
            continue
        if "null" in value.casefold() or "undefined" in value.casefold() or _HTML_RE.search(value):
            _issue(issues, "SPANISH_RESIDUAL", sku, field, actual=value[:160])


def _official_label_tokens(raw_tags: object) -> list[str]:
    return [
        value for part in str(raw_tags or "").split("|")
        if (value := part.strip())
        and not re.fullmatch(r"[−–-]?\s*\d+(?:[.,]\d+)?\s*%", value)
    ]


def _normalize_official_label(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def _official_labels_from_chinese_remarks(remarks: object) -> list[str]:
    match = re.search(r"(?:^|[；;])\s*官网官方标签：\s*([^；;]*)", str(remarks or ""))
    if match is None:
        return []
    labels = []
    for part in match.group(1).split("|"):
        label = re.sub(r"\s*（[^（）]*）\s*$", "", part).strip()
        if label and not re.fullmatch(r"[−–-]?\s*\d+(?:[.,]\d+)?\s*%", label):
            labels.append(label)
    return labels


def _check_zh_official_labels(
    issues: list[dict[str, Any]], sku: str, raw_tags: object, remarks: object,
) -> None:
    """Require all non-discount source labels to survive with source identity."""
    source_labels = _official_label_tokens(raw_tags)
    target_labels = _official_labels_from_chinese_remarks(remarks)
    source_norm = [_normalize_official_label(label) for label in source_labels]
    target_norm = [_normalize_official_label(label) for label in target_labels]
    if source_norm == target_norm:
        return
    code = (
        "OFFICIAL_LABEL_DROPPED" if source_norm and not target_norm else
        "OFFICIAL_LABEL_HALLUCINATED" if target_norm and not source_norm else
        "OFFICIAL_LABEL_MISMATCH"
    )
    _issue(
        issues, code, sku, "备注",
        source_labels=source_labels, target_labels=target_labels,
    )


def _check_source_anomalies(
    issues: list[dict[str, Any]], sku: str, source: dict[str, Any],
) -> None:
    """Carry source-only anomalies into the export semantic gate.

    These are review findings, not translation failures: source evidence is
    retained and the row remains available for non-Gold research output, while
    an unresolved anomaly prevents a Gold claim.
    """
    source_for_consistency = {
        "name": source.get("name") or source.get("name_es"),
        "cat1": source.get("cat1") or source.get("cat1_es"),
        "cat2": source.get("cat2") or source.get("cat2_es"),
        "spec": source.get("spec") or source.get("spec_es"),
        "description": source.get("description") or source.get("desc_es"),
        "details": source.get("details") or source.get("details_es"),
    }
    for evidence in source_consistency_evidence(source_for_consistency):
        _issue(
            issues, "SOURCE_ANOMALY_REVIEW", sku,
            str(evidence.get("source_field") or "source"),
            review_only=True,
            anomaly_code=evidence.get("code"),
            evidence=evidence,
        )


def _check_zh_provenance(
    issues: list[dict[str, Any]], sku: str, source: dict[str, Any], out: dict[str, Any],
    terminology_rules: dict[str, Any], *, allowed_brand_phrases: Iterable[str] = (),
    no_brand_policy: bool = False, description_policy: dict[str, Any] | None = None,
    approved_terms: Iterable[dict[str, str]] = (),
) -> None:
    field_map = {"标题": "name", "分类1": "cat1", "分类2": "cat2", "规格": "spec", "描述": "description", "产品详情": "details"}
    source_field_map = {
        "name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es", "spec": "spec_es",
        "description": "desc_es", "details": "details_es",
    }
    provenance = source.get("_localization_provenance") or {}
    source_hash = _text(source.get("source_hash"))
    for output_field, field_name in field_map.items():
        meta = provenance.get(field_name) or {}
        status = _text(meta.get("review_status")).upper()
        value = _text(out.get(output_field))
        if status not in APPROVED_ZH_STATUSES:
            _issue(issues, "UNAPPROVED_ZH", sku, field_name, status=status or "MISSING")
        approved_by = _text(meta.get("approved_by"))
        approved_at = _text(meta.get("approved_at"))
        if not approved_by or not _valid_approval_timestamp(approved_at):
            _issue(
                issues, "APPROVAL_PROVENANCE_MISSING", sku, field_name,
                approved_by_present=bool(approved_by), approved_at_present=bool(approved_at),
                approved_at_valid=_valid_approval_timestamp(approved_at),
            )
        freshness_status = _text(meta.get("freshness_status")).upper()
        if freshness_status not in CURRENT_ZH_FRESHNESS_STATUSES:
            _issue(issues, "ZH_FRESHNESS_INVALID", sku, field_name,
                   freshness_status=freshness_status or "MISSING")
        field_hash = _text(meta.get("source_hash"))
        expected_field_hash = ""
        source_fields_present = any(
            key in source for key in ("name_es", "cat1_es", "cat2_es", "spec_es", "desc_es", "details_es")
        )
        try:
            expected_field_hash = localization_field_source_hash(source, field_name) if source_fields_present else source_hash
        except (KeyError, ValueError):
            expected_field_hash = source_hash
        if not field_hash or (expected_field_hash and field_hash != expected_field_hash):
            _issue(issues, "STALE_ZH", sku, field_name, source_hash=expected_field_hash, field_hash=field_hash)
            _issue(issues, "SOURCE_HASH_MISMATCH", sku, field_name, source_hash=expected_field_hash, field_hash=field_hash)
        source_value = _text(source.get(source_field_map[field_name]))
        guard = validate_model_output(
            {field_name: source_value}, {field_name: value}, expected_fields=(field_name,),
            allowed_brand_phrases=allowed_brand_phrases,
        )
        for code in guard.field_reasons.get(field_name, ()):
            if code not in _ZH_GUARD_CODES:
                continue
            if code == "BRAND_RETAINED" and not no_brand_policy:
                continue
            payload = {"source": source_value[:240], "actual": value[:240]}
            if code == "BRAND_RETAINED":
                payload["brands"] = list(allowed_brand_phrases)
            _issue(issues, code, sku, field_name, **payload)
        if (
            field_name == "name" and no_brand_policy
            and has_unresolved_chinese_brand_marker(value, allowed_brand_phrases)
        ):
            _issue(issues, "UNRESOLVED_CHINESE_BRAND_MARKER", sku, field_name,
                   actual=value[:240], confirmed_source_brands=list(allowed_brand_phrases))
        for finding in inspect_approved_term_candidate(field_name, source_value, value, approved_terms):
            issues.append({"sku": sku, **finding})
        if field_name in {"name", "description"}:
            source_record = {
                "name": _text(source.get("name_es")),
                "description": _text(source.get("desc_es")),
            }
            _, semantic_findings = inspect_source_facts(source_record, {field_name: value})
            if semantic_findings:
                _issue(issues, "SOURCE_FACT_REVIEW_REQUIRED", sku, field_name,
                       findings=semantic_findings)
        if field_name == "description" and description_policy is not None:
            finding = description_compression_finding(source_value, value, description_policy)
            if finding:
                payload = {key: item for key, item in finding.items() if key != "code"}
                _issue(issues, "DESCRIPTION_COMPRESSION_REVIEW", sku, field_name, **payload)
        if field_name == "details" and source_value and value:
            proposed, terminology_flags = repair_detail_candidate(
                source_value, value, terminology_rules,
                context={
                    "name_es": source.get("name_es", ""),
                    "cat1_es": source.get("cat1_es", ""),
                    "cat2_es": source.get("cat2_es", ""),
                },
            )
            if proposed != value:
                _issue(issues, "DETAIL_TERMINOLOGY_MISMATCH", sku, field_name,
                       proposed=proposed[:600], rules_version=terminology_rules.get("version"))
            if any(
                flag.startswith("DETAIL_PAIR_")
                or flag.startswith("DETAIL_VALUE_TRANSLATION_UNRECOGNIZED:")
                or flag.startswith("DETAIL_SOURCE_VALUE_UNMAPPED_REVIEW:")
                or flag == "DETAIL_CONTEXT_REQUIRED"
                   for flag in terminology_flags):
                _issue(issues, "DETAIL_TERMINOLOGY_REVIEW", sku, field_name,
                       flags=list(terminology_flags))

    source_term_fields = {
        "name": _text(source.get("name_es")), "spec": _text(source.get("spec_es")),
        "description": _text(source.get("desc_es")), "details": _text(source.get("details_es")),
    }
    target_term_fields = {
        "name": _text(out.get("标题")), "spec": _text(out.get("规格")),
        "description": _text(out.get("描述")), "details": _text(out.get("产品详情")),
    }
    for finding in inspect_approved_term_cross_field_movement(
        source_term_fields, target_term_fields, approved_terms,
    ):
        issues.append({"sku": sku, **finding})


def _issue_if(issues: list[dict[str, Any]], code: str, condition: bool, **payload: Any) -> None:
    if condition:
        _issue(issues, code, "", "", **payload)


def _valid_approval_timestamp(value: str) -> bool:
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _issue(issues: list[dict[str, Any]], code: str, sku: str, field: str, **payload: Any) -> None:
    issues.append({"code": code, "sku": sku, "field": field, **payload})


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None
