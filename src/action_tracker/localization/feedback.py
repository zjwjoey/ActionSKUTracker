"""Human-correction mining; produces candidates but never auto-approves them."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Mapping


def mine_family_feedback(rows: Iterable[Mapping[str, Any]], *, min_occurrences: int = 3) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for row in rows:
        family_id = str(row.get("family_id") or "UNKNOWN")
        field_name = str(row.get("field_name") or "")
        context_key = str(row.get("context_key") or "")
        source_term = str(row.get("source_term") or "")
        old_target = str(row.get("old_target") or "")
        human_target = str(row.get("human_target") or "")
        if not field_name or not source_term or not human_target or human_target == old_target:
            continue
        key = (family_id, field_name, context_key, source_term.casefold(), human_target)
        item = groups.setdefault(key, {"family_id": family_id, "field_name": field_name, "context_key": context_key, "source_term": source_term, "old_target": old_target, "human_target": human_target, "occurrence_count": 0, "sample_skus": [], "confidence": 0.0, "suggested_action": "REVIEW_CANDIDATE"})
        item["occurrence_count"] += 1
        sku = str(row.get("sku") or "")
        if sku and sku not in item["sample_skus"] and len(item["sample_skus"]) < 10:
            item["sample_skus"].append(sku)
    result = []
    for item in groups.values():
        count = int(item["occurrence_count"])
        if count < min_occurrences:
            continue
        item["confidence"] = min(0.99, 0.6 + 0.1 * min(count, 4))
        result.append(item)
    return sorted(result, key=lambda item: (-int(item["occurrence_count"]), item["family_id"], item["source_term"]))

