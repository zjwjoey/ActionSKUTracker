"""Controlled aggregation and proposal generation for detail-rule reviews.

The audit queue is occurrence evidence.  This module turns it into stable,
source-hash-bound rule candidates, and turns owner decisions into a proposal
document only.  It never edits the terminology configuration or any product
table.
"""
from __future__ import annotations

import hashlib
import json
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from .detail_review_contract import validate_detail_rule_decisions


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _norm_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = "".join(char for char in unicodedata.normalize("NFD", text) if unicodedata.category(char) != "Mn")
    return " ".join(text.split())


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _context_scope(row: Mapping[str, Any]) -> tuple[str, ...]:
    """Return a stable, review-visible product context for a queue row.

    Older queues only contain the source key/value.  They remain readable, but
    are deliberately assigned an explicit ``<unknown>`` scope instead of being
    silently treated as a global rule.  New queues may provide either a
    ``context_scope`` JSON list or the individual source context columns.
    """
    raw = row.get("context_scope") or row.get("context")
    values: list[Any] = _json_list(raw)
    if not values and isinstance(raw, Mapping):
        values = [raw]
    if not values:
        values = [{
            key: row.get(key)
            for key in ("name_es", "cat1_es", "cat2_es", "product_type")
            if str(row.get(key) or "").strip()
        }]
    normalized: set[str] = set()
    for value in values:
        if isinstance(value, str) and value.lstrip().startswith("{"):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                pass
        if isinstance(value, Mapping):
            item = {
                key: " ".join(str(value.get(key) or "").split()).casefold()
                for key in ("name_es", "cat1_es", "cat2_es", "product_type")
                if str(value.get(key) or "").strip()
            }
            normalized.add(_canonical(item) if item else "<unknown>")
        elif str(value).strip():
            normalized.add(" ".join(str(value).split()).casefold())
    return tuple(sorted(normalized or {"<unknown>"}))


def _target_from_regression(case: Any, kind: str) -> str:
    expected = str(getattr(case, "expected_zh", "") or "").strip()
    if kind not in {"SOURCE_KEY", "SOURCE_VALUE"} or not expected:
        return expected
    parts = expected.replace("：", ":").split(":", 1)
    if len(parts) == 2:
        return parts[0].strip() if kind == "SOURCE_KEY" else parts[1].strip()
    return expected


def _validate_regression_bindings(
    *, candidate: Mapping[str, Any], decision: Mapping[str, Any],
    regression_cases: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Validate that an approved rule has owner-confirmed regression proof."""
    errors: list[dict[str, str]] = []
    case_ids = [str(item).strip() for item in _json_list(decision.get("regression_case_ids")) if str(item).strip()]
    if not case_ids:
        errors.append({"code": "REGRESSION_CASE_REQUIRED", "review_id": str(decision.get("review_id") or "")})
        return errors
    kind = str(candidate.get("review_kind") or "")
    source_key = _norm_text(candidate.get("source_key_normalized"))
    source_value = _norm_text(candidate.get("source_value_normalized"))
    approved = _norm_text(decision.get("approved_target"))
    matched = False
    for case_id in case_ids:
        case = regression_cases.get(case_id)
        if case is None:
            errors.append({"code": "REGRESSION_CASE_UNKNOWN", "review_id": str(decision.get("review_id") or ""), "case_id": case_id})
            continue
        if str(getattr(case, "field", "")) != "details":
            continue
        if _norm_text(getattr(case, "source_key", "")) != source_key:
            continue
        case_value = _norm_text(getattr(case, "source_value", ""))
        if kind == "SOURCE_VALUE" and case_value != source_value:
            continue
        expected = _norm_text(_target_from_regression(case, kind))
        if expected == approved:
            matched = True
            break
    if not matched:
        errors.append({"code": "REGRESSION_CASE_DOES_NOT_CONFIRM_RULE", "review_id": str(decision.get("review_id") or "")})
    return errors


def build_detail_rule_candidates(
    queue_rows: Iterable[Mapping[str, Any]],
    *,
    source_sha256: str,
    target_sha256: str,
    rules_sha256: str,
) -> list[dict[str, Any]]:
    """Aggregate repeated queue evidence into deterministic rule candidates."""
    grouped: dict[tuple[str, str, str, str, tuple[str, ...]], dict[str, Any]] = {}
    for raw in queue_rows:
        row = dict(raw)
        kind = str(row.get("review_kind") or "").strip()
        key = str(row.get("source_key_normalized") or "").strip()
        value = str(row.get("source_value_normalized") or "").strip()
        reasons = str(row.get("review_reasons") or "").strip()
        context_scope = _context_scope(row)
        identity = (kind, key, value, reasons if kind == "PAIR_ALIGNMENT" else "", context_scope)
        item = grouped.setdefault(identity, {
            "review_kind": kind,
            "source_key_normalized": key,
            "source_value_normalized": value,
            "review_reasons": identity[3] or reasons,
            "context_scope": set(),
            "occurrences": 0,
            "rule_covered_occurrences": 0,
            "uncovered_occurrences": 0,
            "sku_count": 0,
            "sku_examples": set(),
            "observed_target_candidates": Counter(),
            "queue_review_ids": set(),
        })
        item["occurrences"] += int(row.get("occurrences") or 0)
        item["rule_covered_occurrences"] += int(row.get("rule_covered_occurrences") or 0)
        item["uncovered_occurrences"] += int(row.get("uncovered_occurrences") or 0)
        item["sku_examples"].update(str(sku) for sku in _json_list(row.get("sku_examples")) if str(sku))
        item["context_scope"].update(context_scope)
        item["queue_review_ids"].add(str(row.get("review_id") or ""))
        observed = _json_list(row.get("observed_target_candidates"))
        for candidate in observed:
            item["observed_target_candidates"][_canonical(candidate)] += 1

    output: list[dict[str, Any]] = []
    for item in grouped.values():
        identity = {
            "review_kind": item["review_kind"],
            "source_key_normalized": item["source_key_normalized"],
            "source_value_normalized": item["source_value_normalized"],
            "review_reasons": item["review_reasons"],
            "context_scope": sorted(item["context_scope"]),
            "source_sha256": source_sha256,
            "target_sha256": target_sha256,
            "detail_rules_sha256": rules_sha256,
        }
        observed = []
        for raw, count in sorted(item["observed_target_candidates"].items()):
            try:
                candidate = json.loads(raw)
            except json.JSONDecodeError:
                candidate = raw
            observed.append({"candidate": candidate, "occurrences": count})
        output.append({
            "candidate_id": f"detail-rule-{_sha(identity)[:24]}",
            **identity,
            "occurrences": item["occurrences"],
            "rule_covered_occurrences": item["rule_covered_occurrences"],
            "uncovered_occurrences": item["uncovered_occurrences"],
            "sku_count": len(item["sku_examples"]),
            "sku_examples": sorted(item["sku_examples"])[:20],
            "queue_review_ids": sorted(item["queue_review_ids"]),
            "observed_target_candidates": observed,
            "decision_status": "NEEDS_OWNER_REVIEW",
            "auto_apply": False,
        })
    return sorted(output, key=lambda row: (row["review_kind"], row["source_key_normalized"], row["source_value_normalized"], row["candidate_id"]))


def build_detail_rule_proposals(
    queue_rows: Iterable[Mapping[str, Any]],
    decision_rows: Iterable[Mapping[str, Any]],
    *,
    source_sha256: str,
    target_sha256: str,
    rules_sha256: str,
    candidate_rows: Iterable[Mapping[str, Any]] | None = None,
    regression_cases: Iterable[Any] = (),
    require_regression: bool = True,
) -> dict[str, Any]:
    """Return owner-approved rule proposals without mutating configuration.

    Decisions may target aggregated ``candidate_id`` values.  Legacy
    ``review_id`` decisions remain readable for migration, but the CLI uses
    candidate IDs and requires a matching versioned regression case.
    """
    queue = [dict(row) for row in queue_rows]
    decisions = [dict(row) for row in decision_rows if str(row.get("decision") or "").strip()]
    candidates = [dict(row) for row in (candidate_rows or build_detail_rule_candidates(
        queue, source_sha256=source_sha256, target_sha256=target_sha256, rules_sha256=rules_sha256,
    ))]
    candidate_ids = {str(row.get("candidate_id") or "") for row in candidates}
    decision_ids = {str(row.get("review_id") or "") for row in decisions}
    candidate_mode = bool(decision_ids and decision_ids.issubset(candidate_ids))
    review_rows = candidates if candidate_mode else queue
    if review_rows is candidates:
        review_rows = [{**row, "review_id": str(row.get("candidate_id") or "")} for row in candidates]
    validation = validate_detail_rule_decisions(
        review_rows, decisions, source_sha256=source_sha256,
        target_sha256=target_sha256, rules_sha256=rules_sha256,
    )
    if not validation["accepted"]:
        return {
            "accepted": False,
            "errors": validation["errors"],
            "auto_apply": False,
            "rules_write": False,
            "review_status": "NOT_STARTED" if not decisions else "REVIEW_BLOCKED",
            "proposals": [],
        }
    by_id = {str(row.get("candidate_id") or row.get("review_id") or ""): row for row in review_rows}
    cases = {str(getattr(case, "case_id", "")): case for case in regression_cases}
    regression_errors: list[dict[str, str]] = []
    proposals: list[dict[str, Any]] = []
    for decision in validation["approved_rows"]:
        queue_row = by_id[str(decision["review_id"])]
        kind = str(queue_row.get("review_kind") or "")
        source_key = str(queue_row.get("source_key_normalized") or "")
        source_value = str(queue_row.get("source_value_normalized") or "")
        approved_target = str(decision.get("approved_target") or "").strip()
        if require_regression and kind in {"SOURCE_KEY", "SOURCE_VALUE"}:
            regression_errors.extend(_validate_regression_bindings(
                candidate=queue_row, decision=decision, regression_cases=cases,
            ))
        if kind == "SOURCE_KEY":
            proposal = {"section": "key_translations", "source_key": source_key, "target_key": approved_target}
        elif kind == "SOURCE_VALUE":
            proposal = {
                "section": "value_translations", "source_key": source_key,
                "source_value": source_value, "target_value": approved_target,
            }
        else:
            # Pair alignment and source anomalies are evidence, not safe global
            # terminology rules.  They must be handled by a separate anomaly
            # decision flow and cannot become dictionary entries.
            continue
        proposal_row = {"review_id": decision["review_id"], **proposal, "auto_apply": False}
        if candidate_mode:
            proposal_row.update({
                "candidate_id": str(queue_row.get("candidate_id") or ""),
                "context_scope": queue_row.get("context_scope") or ["<unknown>"],
                "regression_case_ids": _json_list(decision.get("regression_case_ids")),
                "approved_by": str(decision.get("reviewer") or "").strip(),
                "approved_at": str(decision.get("reviewed_at") or "").strip(),
            })
        proposals.append(proposal_row)
    if regression_errors:
        return {
            "accepted": False, "errors": regression_errors, "auto_apply": False,
            "rules_write": False, "proposals": [], "review_status": "REGRESSION_REQUIRED",
        }
    return {
        "accepted": True,
        "errors": [],
        "auto_apply": False,
        "rules_write": False,
        "source_sha256": source_sha256,
        "target_sha256": target_sha256,
        "detail_rules_sha256": rules_sha256,
        "review_status": "OWNER_REVIEWED" if decisions else "NOT_STARTED",
        "proposals": proposals,
    }


__all__ = ["build_detail_rule_candidates", "build_detail_rule_proposals"]
