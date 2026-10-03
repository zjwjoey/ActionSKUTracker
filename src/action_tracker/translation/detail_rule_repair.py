"""Expand approved detail rules into a source-bound repair preview manifest.

This module only creates a manifest.  It does not write SQLite, Excel, a
dictionary, or a localization table.  The existing repair service remains the
only component that can preview/apply a field-level patch.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
import json
import unicodedata
from typing import Any

from ..localization.repair_service import value_hash
from ..products.details_parser import parse_details
from ..services.hashing import localization_field_source_hash


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKC", _text(value)).casefold()
    text = "".join(char for char in unicodedata.normalize("NFD", text) if unicodedata.category(char) != "Mn")
    return " ".join(text.split())


def _record_context_token(record: Mapping[str, Any]) -> str:
    return json.dumps({
        key: _norm(record.get(key))
        for key in ("name_es", "cat1_es", "cat2_es", "product_type")
        if _text(record.get(key)).strip()
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _proposal_context_matches(proposal: Mapping[str, Any], record: Mapping[str, Any]) -> bool:
    raw = proposal.get("context_scope")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = [raw]
    if not isinstance(raw, list) or not raw or "<unknown>" in {str(item) for item in raw}:
        return False
    token = _record_context_token(record)
    return token in {str(item) for item in raw}


def build_detail_rule_repair_manifest(
    records: Iterable[Mapping[str, Any]],
    proposals: Iterable[Mapping[str, Any]],
    *,
    policy_manifest_hash: str,
) -> dict[str, Any]:
    """Build one pair-scoped preview row per approved source occurrence.

    A source/target pair count mismatch, missing approval identity, unknown
    context, or duplicate target is reported as a blocked occurrence.  No
    occurrence is interpreted as a missing SKU and no target cell is changed.
    """
    materialized = [dict(row) for row in records]
    proposal_rows = [dict(row) for row in proposals]
    manifest: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    seen_targets: set[tuple[str, int]] = set()

    for proposal in proposal_rows:
        kind = _text(proposal.get("review_kind") or proposal.get("source_kind"))
        if not kind:
            if proposal.get("section") == "key_translations":
                kind = "SOURCE_KEY"
            elif proposal.get("section") == "value_translations":
                kind = "SOURCE_VALUE"
        source_key = _norm(proposal.get("source_key"))
        source_value = _norm(proposal.get("source_value"))
        approved_by = _text(proposal.get("approved_by")).strip()
        if not approved_by:
            blocked.append({"candidate_id": proposal.get("candidate_id"), "reason": "OWNER_APPROVAL_MISSING"})
            continue
        if kind not in {"SOURCE_KEY", "SOURCE_VALUE"} or not source_key:
            blocked.append({"candidate_id": proposal.get("candidate_id"), "reason": "UNSUPPORTED_DETAIL_RULE"})
            continue
        for record in materialized:
            sku = _text(record.get("sku") or record.get("official_sku")).strip()
            source = _text(record.get("details_es"))
            target = _text(record.get("details_zh"))
            if not _proposal_context_matches(proposal, record):
                continue
            source_pairs = parse_details(source)
            target_pairs = parse_details(target)
            if len(source_pairs) != len(target_pairs):
                if any(_norm(pair.key_es) == source_key for pair in source_pairs):
                    blocked.append({"candidate_id": proposal.get("candidate_id"), "sku": sku, "reason": "DETAIL_STRUCTURE_CHANGED"})
                continue
            for index, source_pair in enumerate(source_pairs):
                if _norm(source_pair.key_es) != source_key:
                    continue
                if kind == "SOURCE_VALUE" and _norm(source_pair.value_es) != source_value:
                    continue
                target_pair = target_pairs[index]
                target_key = _text(proposal.get("target_key")) if kind == "SOURCE_KEY" else _text(target_pair.key_es)
                target_value = _text(proposal.get("target_value")) if kind == "SOURCE_VALUE" else _text(target_pair.value_es)
                target_id = (sku, index)
                if target_id in seen_targets:
                    blocked.append({"candidate_id": proposal.get("candidate_id"), "sku": sku, "reason": "DUPLICATE_REPAIR_TARGET"})
                    continue
                seen_targets.add(target_id)
                manifest.append({
                    "candidate_id": proposal.get("candidate_id"),
                    "sku": sku,
                    "field": "details",
                    "operation": "REPLACE_DETAILS_PAIR",
                    "detail_pair_index": index,
                    "expected_target_key": _text(target_pair.key_es),
                    "expected_target_value": _text(target_pair.value_es),
                    "target_key": target_key,
                    "target_value": target_value,
                    "expected_source_hash": localization_field_source_hash(record, "details"),
                    "expected_target_hash": value_hash(target),
                    "policy_manifest_hash": policy_manifest_hash,
                    "approved_by": approved_by,
                    "approved_at": _text(proposal.get("approved_at")),
                    "reason": f"approved_detail_rule:{proposal.get('candidate_id') or proposal.get('review_id')}",
                })
    return {
        "schema": "ACTION_DETAIL_RULE_REPAIR_MANIFEST_V1",
        "policy_manifest_hash": policy_manifest_hash,
        "rows": manifest,
        "blocked": blocked,
        "matched_occurrences": len(manifest),
        "blocked_occurrences": len(blocked),
        "production_apply": False,
        "master_writes": 0,
    }


__all__ = ["build_detail_rule_repair_manifest"]
