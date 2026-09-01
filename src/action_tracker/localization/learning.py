from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

STATES = ("UNKNOWN", "AI_CANDIDATE", "EVIDENCE_ACCUMULATED", "EVIDENCE_CONFLICT", "HUMAN_REVIEWED", "LOCKED", "REJECTED")


def candidate_id(semantic_type: str, source_term: str, zh_value: str) -> str:
    return hashlib.sha256(json.dumps([semantic_type.upper(), source_term.strip().casefold(), zh_value.strip()], ensure_ascii=False).encode()).hexdigest()


def aggregate_candidates(rows: Iterable[dict[str, Any]], directory: Path) -> dict[str, Any]:
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    grouped: dict[str, dict[str, Any]] = {}
    evidence_maps: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
    for row in rows:
        semantic_type = str(row.get("semantic_type") or "UNKNOWN").upper()
        source_term = str(row.get("source_term") or row.get("normalized_source") or "").strip()
        zh_value = str(row.get("zh_value") or row.get("canonical_zh") or "").strip()
        key = candidate_id(semantic_type, source_term, zh_value)
        item = grouped.setdefault(key, {"candidate_id": key, "knowledge_type": semantic_type, "semantic_type": semantic_type, "source_term": source_term, "normalized_source": source_term.casefold(), "zh_value": zh_value, "semantic_value": zh_value, "preferred_target": row.get("preferred_target", ""), "allowed_targets": row.get("allowed_targets", ""), "category_context": row.get("category_context", ""), "occurrence_count": 0, "evidence_skus": set(), "evidence": [], "source_examples": set(), "provider": row.get("provider", "deterministic"), "model": row.get("model", ""), "prompt_version": row.get("prompt_version", ""), "policy_version": row.get("policy_version", "CHINESE_LOCALIZATION_STANDARD_V1"), "confidence": row.get("confidence", ""), "validator_status": row.get("validator_status", "PASS"), "review_status": row.get("review_status", "PENDING"), "status": "AI_CANDIDATE", "source_hash": row.get("source_hash", ""), "source_run_id": row.get("source_run_id", ""), "source_commit_id": row.get("source_commit_id", ""), "request_hash": row.get("request_hash", ""), "response_hash": row.get("response_hash", ""), "created_at": datetime.now(timezone.utc).isoformat(), "updated_at": datetime.now(timezone.utc).isoformat()})
        evidence_maps.setdefault(key, {})
        item["occurrence_count"] += 1; item["evidence_skus"].add(str(row.get("sku") or ""))
        sku = str(row.get("sku") or "").strip()
        evidence_hash = str(row.get("source_hash") or "").strip()
        if sku:
            evidence_key = (sku, evidence_hash)
            evidence_maps[key].setdefault(evidence_key, {"sku": sku, "source_hash": evidence_hash,
                "source_run_id": str(row.get("source_run_id") or ""),
                "source_commit_id": str(row.get("source_commit_id") or ""),
                "source_example": str(row.get("source_example") or source_term)})
        if row.get("source_example") or source_term:
            item["source_examples"].add(str(row.get("source_example") or source_term))
    output = []
    for item in grouped.values():
        evidence = sorted(evidence_maps[item["candidate_id"]].values(), key=lambda x: (x["sku"], x["source_hash"]))
        item["evidence"] = evidence
        item["evidence_skus"] = sorted(s for s in item["evidence_skus"] if s)
        item["source_examples"] = sorted(s for s in item["source_examples"] if s)
        item["status"] = "EVIDENCE_ACCUMULATED" if item["occurrence_count"] > 1 else item["status"]
        if len({e["sku"] for e in evidence}) < len(evidence):
            item["status"] = "EVIDENCE_CONFLICT"
        # Keep source_hash only as a legacy display value.  Freshness uses the
        # structured per-SKU evidence list below.
        if len(evidence) == 1:
            item["source_hash"] = evidence[0]["source_hash"]
        elif len(evidence) > 1:
            item["source_hash"] = ""
        output.append(item)
    output.sort(key=lambda x: (x["semantic_type"], x["source_term"], x["zh_value"]))
    path = directory / "learning_candidates.csv"
    headers = ["candidate_id", "knowledge_type", "semantic_type", "source_term", "normalized_source", "zh_value", "semantic_value", "preferred_target", "allowed_targets", "category_context", "occurrence_count", "evidence_skus", "evidence_json", "source_examples", "provider", "model", "prompt_version", "policy_version", "confidence", "validator_status", "review_status", "status", "source_hash", "source_run_id", "source_commit_id", "request_hash", "response_hash", "created_at", "updated_at"]
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers); writer.writeheader()
        for item in output:
            row = {key: item.get(key, "") for key in headers}
            row.update({"evidence_skus": "|".join(item["evidence_skus"]), "evidence_json": json.dumps(item["evidence"], ensure_ascii=False, sort_keys=True), "source_examples": "|".join(item["source_examples"])})
            writer.writerow(row)
    return {"path": str(path), "count": len(output), "rows": output}


def promotion_decision(candidate: dict[str, Any], *, human_approved: bool = False) -> dict[str, Any]:
    old = str(candidate.get("status") or "UNKNOWN")
    if old == "EVIDENCE_CONFLICT":
        return {"candidate_id": candidate.get("candidate_id"), "old_state": old,
                "new_state": old, "promoted": False,
                "promotion_blocked": True, "reason": "EVIDENCE_CONFLICT",
                "policy_version": "CHINESE_LOCALIZATION_STANDARD_V1"}
    safe_auto = old == "EVIDENCE_ACCUMULATED" and str(candidate.get("semantic_type")) in {"STANDARD_UNIT", "TECH_TOKEN"}
    if human_approved: new = "LOCKED"
    elif safe_auto: new = "EVIDENCE_ACCUMULATED"
    else: new = old
    return {"candidate_id": candidate.get("candidate_id"), "old_state": old, "new_state": new, "promoted": new != old, "promotion_blocked": False, "policy_version": "CHINESE_LOCALIZATION_STANDARD_V1"}


def persist_promotion(candidate: dict[str, Any], directory: Path, *, human_approved: bool = False) -> dict[str, Any]:
    """Persist an auditable promotion decision; never silently edits baseline."""
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    decision = promotion_decision(candidate, human_approved=human_approved)
    from .promotion import promotion_manifest
    decision.update(promotion_manifest(candidate, decision["old_state"], decision["new_state"]))
    path = directory / f"promotion_{candidate.get('candidate_id')}.json"
    path.write_text(json.dumps({**decision, "candidate": candidate}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**decision, "manifest_path": str(path)}
