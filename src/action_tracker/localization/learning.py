from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

STATES = ("UNKNOWN", "AI_CANDIDATE", "EVIDENCE_ACCUMULATED", "HUMAN_REVIEWED", "LOCKED", "REJECTED")


def candidate_id(semantic_type: str, source_term: str, zh_value: str) -> str:
    return hashlib.sha256(json.dumps([semantic_type, source_term, zh_value], ensure_ascii=False).encode()).hexdigest()


def aggregate_candidates(rows: Iterable[dict[str, Any]], directory: Path) -> dict[str, Any]:
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = candidate_id(str(row.get("semantic_type") or "UNKNOWN"), str(row.get("source_term") or ""), str(row.get("zh_value") or ""))
        item = grouped.setdefault(key, {"candidate_id": key, "semantic_type": row.get("semantic_type", "UNKNOWN"), "source_term": row.get("source_term", ""), "zh_value": row.get("zh_value", ""), "occurrence_count": 0, "evidence_skus": set(), "status": "AI_CANDIDATE", "created_at": datetime.now(timezone.utc).isoformat()})
        item["occurrence_count"] += 1; item["evidence_skus"].add(str(row.get("sku") or ""))
    output = []
    for item in grouped.values():
        item["evidence_skus"] = sorted(s for s in item["evidence_skus"] if s)
        item["status"] = "EVIDENCE_ACCUMULATED" if item["occurrence_count"] > 1 else item["status"]
        output.append(item)
    output.sort(key=lambda x: (x["semantic_type"], x["source_term"], x["zh_value"]))
    path = directory / "learning_candidates.csv"
    headers = ["candidate_id", "semantic_type", "source_term", "zh_value", "occurrence_count", "evidence_skus", "status", "created_at"]
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers); writer.writeheader()
        for item in output: writer.writerow({**item, "evidence_skus": "|".join(item["evidence_skus"])})
    return {"path": str(path), "count": len(output), "rows": output}


def promotion_decision(candidate: dict[str, Any], *, human_approved: bool = False) -> dict[str, Any]:
    old = str(candidate.get("status") or "UNKNOWN")
    safe_auto = old == "EVIDENCE_ACCUMULATED" and str(candidate.get("semantic_type")) in {"STANDARD_UNIT", "TECH_TOKEN"}
    if human_approved: new = "LOCKED"
    elif safe_auto: new = "EVIDENCE_ACCUMULATED"
    else: new = old
    return {"candidate_id": candidate.get("candidate_id"), "old_state": old, "new_state": new, "promoted": new != old, "policy_version": "CHINESE_LOCALIZATION_STANDARD_V1"}
