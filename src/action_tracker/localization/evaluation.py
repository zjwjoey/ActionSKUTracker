"""Offline, field-level evaluation for frozen translation Gold.

The evaluator deliberately does not approve, promote or write a Gold row.  It
only compares a prediction with an Owner-approved target and reports facts
that matter for Action product data rather than relying on string exactness.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Iterable, Mapping

from .contracts import SourceFacts
from .qa import audit_translation
from .policy import FIXED_CAT1, has_ordinary_spanish


_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")
_UNIT_RE = re.compile(r"\d+(?:[.,]\d+)?\s*(?:mg|mcg|μg|kg|g|ml|l|cm|mm|m|v|w|hz|%|uds?\.?|unidades?)\b", re.I)
_MODEL_RE = re.compile(r"\b[A-Z]{1,8}[-/]?[A-Z0-9]{1,12}\b")


def _counter(pattern: re.Pattern[str], value: object) -> Counter[str]:
    return Counter(str(item).replace(",", ".").casefold() for item in pattern.findall(str(value or "")))


def _row_value(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        if row.get(key) is not None:
            return str(row.get(key) or "")
    return ""


def evaluate_frozen_gold(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Evaluate prediction/Gold rows without mutating registry or production.

    Accepted row aliases are intentionally small and explicit: ``sku``,
    ``field_name``/``field``, ``source``/``source_text``, ``prediction`` and
    ``gold``/``target``/``target_text``.  Rows with missing identity or values
    are counted as rejected rather than being silently skipped.
    """
    rows = [dict(row) for row in rows]
    metrics = Counter()
    issues: list[dict[str, Any]] = []
    for row in rows:
        sku = _row_value(row, "sku", "official_sku")
        field = _row_value(row, "field_name", "field")
        source = _row_value(row, "source", "source_text", "source_es")
        prediction = _row_value(row, "prediction", "predicted", "actual")
        gold = _row_value(row, "gold", "target", "target_text", "expected")
        if not sku or not field or not gold:
            metrics["invalid_rows"] += 1
            issues.append({"sku": sku, "field": field, "rule_id": "GOLD_ROW_INVALID"})
            continue
        metrics["rows"] += 1
        if prediction.strip() == gold.strip():
            metrics["field_exact"] += 1
        else:
            metrics["field_non_exact"] += 1
        source_numbers, prediction_numbers = _counter(_NUM_RE, source), _counter(_NUM_RE, prediction)
        if source_numbers == prediction_numbers:
            metrics["number_preservation"] += 1
        else:
            metrics["number_mismatch"] += 1
            issues.append({"sku": sku, "field": field, "rule_id": "NUMBER_MISMATCH", "source": source, "prediction": prediction})
        source_units = _counter(_UNIT_RE, source)
        pred_units = _counter(_UNIT_RE, prediction)
        # Unit labels may be legitimately localized (``uds.`` → ``件``),
        # therefore compare multiplicity and pairing count, not literal
        # language-specific spellings.
        if not source_units or sum(source_units.values()) == sum(pred_units.values()):
            metrics["unit_preservation"] += 1
        else:
            metrics["unit_mismatch"] += 1
        source_models = {item.casefold() for item in _MODEL_RE.findall(source)}
        prediction_folded = prediction.casefold()
        if not source_models or all(item in prediction_folded for item in source_models):
            metrics["model_preservation"] += 1
        else:
            metrics["model_mismatch"] += 1
        if has_ordinary_spanish(prediction, allowed_tokens=set()):
            metrics["spanish_residual"] += 1
        else:
            metrics["spanish_clean"] += 1
        if field == "cat1":
            if prediction in FIXED_CAT1:
                metrics["category_accuracy"] += 1
            else:
                metrics["category_invalid"] += 1
        source_record = {"sku": sku, "name_es": source if field == "name" else "", "cat1_es": source if field == "cat1" else "", "spec_es": source if field == "spec" else "", "desc_es": source if field == "description" else "", "details_es": source if field == "details" else ""}
        qa_findings = audit_translation(SourceFacts.from_record(source_record), {field: prediction}, (field,))
        if qa_findings:
            metrics["qa_findings_on_prediction"] += len(qa_findings)
            issues.extend({"sku": sku, "field": field, **finding.as_dict()} for finding in qa_findings)
    total = int(metrics["rows"])
    metrics["qa_false_positive"] = int(metrics["qa_findings_on_prediction"])
    metrics["qa_false_negative"] = int(metrics["number_mismatch"] + metrics["unit_mismatch"] + metrics["model_mismatch"])
    return {"status": "PASS" if not metrics["invalid_rows"] and not metrics["number_mismatch"] and not metrics["unit_mismatch"] and not metrics["model_mismatch"] else "FAIL", "records_checked": total, "metrics": {key: int(value) for key, value in metrics.items()}, "issues": issues}
