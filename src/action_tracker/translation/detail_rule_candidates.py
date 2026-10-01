"""Controlled aggregation and proposal generation for detail-rule reviews.

The audit queue is occurrence evidence.  This module turns it into stable,
source-hash-bound rule candidates, and turns owner decisions into a proposal
document only.  It never edits the terminology configuration or any product
table.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from .detail_review_contract import validate_detail_rule_decisions


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def build_detail_rule_candidates(
    queue_rows: Iterable[Mapping[str, Any]],
    *,
    source_sha256: str,
    target_sha256: str,
    rules_sha256: str,
) -> list[dict[str, Any]]:
    """Aggregate repeated queue evidence into deterministic rule candidates."""
    grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for raw in queue_rows:
        row = dict(raw)
        kind = str(row.get("review_kind") or "").strip()
        key = str(row.get("source_key_normalized") or "").strip()
        value = str(row.get("source_value_normalized") or "").strip()
        reasons = str(row.get("review_reasons") or "").strip()
        identity = (kind, key, value, reasons if kind == "PAIR_ALIGNMENT" else "")
        item = grouped.setdefault(identity, {
            "review_kind": kind,
            "source_key_normalized": key,
            "source_value_normalized": value,
            "review_reasons": identity[3] or reasons,
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
        item["sku_count"] += int(row.get("sku_count") or 0)
        item["sku_examples"].update(str(sku) for sku in _json_list(row.get("sku_examples")) if str(sku))
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
            "sku_count": item["sku_count"],
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
) -> dict[str, Any]:
    """Return owner-approved rule proposals without mutating configuration."""
    queue = [dict(row) for row in queue_rows]
    decisions = [dict(row) for row in decision_rows if str(row.get("decision") or "").strip()]
    validation = validate_detail_rule_decisions(
        queue, decisions, source_sha256=source_sha256,
        target_sha256=target_sha256, rules_sha256=rules_sha256,
    )
    if not validation["accepted"]:
        return {"accepted": False, "errors": validation["errors"], "auto_apply": False, "proposals": []}
    by_id = {str(row.get("review_id") or ""): row for row in queue}
    proposals: list[dict[str, Any]] = []
    for decision in validation["approved_rows"]:
        queue_row = by_id[str(decision["review_id"])]
        kind = str(queue_row.get("review_kind") or "")
        source_key = str(queue_row.get("source_key_normalized") or "")
        source_value = str(queue_row.get("source_value_normalized") or "")
        approved_target = str(decision.get("approved_target") or "").strip()
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
        proposals.append({"review_id": decision["review_id"], **proposal, "auto_apply": False})
    return {
        "accepted": True,
        "errors": [],
        "auto_apply": False,
        "rules_write": False,
        "source_sha256": source_sha256,
        "target_sha256": target_sha256,
        "detail_rules_sha256": rules_sha256,
        "proposals": proposals,
    }


__all__ = ["build_detail_rule_candidates", "build_detail_rule_proposals"]
