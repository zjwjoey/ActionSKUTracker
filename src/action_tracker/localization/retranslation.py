"""Retranslation Batch V1 candidate generation.

This module deliberately reuses the existing Translation Registry runtime and
shadow/canary report contract.  It never mutates PRIMARY data, creates a
revision, or approves a candidate.  The current Master Chinese value is kept
as a baseline only (``old_zh``), never as translation authority.
"""
from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .contracts import CANONICAL_AI_FIELDS
from .hashes import value_hash
from .runtime import shadow_run


FIELD_TO_ZH = {
    "name": "name_zh",
    "cat1": "cat1_zh",
    "cat2": "cat2_zh",
    "spec": "spec_zh",
    "description": "desc_zh",
    "details": "details_zh",
}

FIELD_TO_ES = {
    "name": "name_es",
    "cat1": "cat1_es",
    "cat2": "cat2_es",
    "spec": "spec_es",
    "description": "desc_es",
    "details": "details_es",
}


def _now_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _snapshot_hash(records: list[Mapping[str, Any]]) -> str:
    payload = [
        {key: record.get(key) for key in ("sku", *FIELD_TO_ES.values())}
        for record in records
    ]
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _registry_identity(resolver: Any, sku: str, source_hash: str, field_name: str, old_value: str) -> dict[str, str]:
    """Return best-effort immutable Registry identity without inventing one."""
    result = {
        "source_version_id": "",
        "old_revision_id": "",
        "approved_revision_id": "",
    }
    registry = getattr(resolver, "registry", None)
    db_path = getattr(resolver, "db_path", None)
    if registry is not None:
        try:
            approved = registry.get_current_approved_revision(sku, field_name, source_hash)
            if approved:
                result["approved_revision_id"] = str(approved.get("revision_id") or "")
        except Exception:
            pass
        db_path = getattr(registry, "path", db_path)
    if not db_path:
        return result
    try:
        from ..database.connection import connect

        canonical = {"name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es", "spec": "spec_es", "description": "desc_es", "details": "details_es"}.get(field_name, field_name)
        with connect(db_path) as db:
            row = db.execute(
                """SELECT s.source_version_id,u.current_revision_id,r.target_text,r.revision_id
                   FROM translation_source_versions s
                   JOIN translation_units u ON u.source_version_id=s.source_version_id
                   LEFT JOIN translation_revisions r ON r.revision_id=u.current_revision_id
                  WHERE s.official_sku=? AND s.source_hash=? AND u.field_name IN (?,?)
                  ORDER BY s.observed_at DESC LIMIT 1""",
                (sku, source_hash, field_name, canonical),
            ).fetchone()
        if row:
            result["source_version_id"] = str(row[0] or "")
            current_revision = str(row[1] or "")
            target_text = str(row[2] or "")
            revision_id = str(row[3] or current_revision)
            # Only claim the old revision when it actually produced the
            # displayed Master value.  Otherwise the Master is a snapshot-only
            # baseline and old_revision_id remains empty.
            if current_revision and value_hash(target_text) == value_hash(old_value):
                result["old_revision_id"] = current_revision
            if result["approved_revision_id"] == "" and current_revision and value_hash(target_text) == value_hash(old_value):
                result["approved_revision_id"] = revision_id
    except Exception:
        # Candidate generation must remain usable for an older DB schema; an
        # absent identity is explicit rather than fabricated.
        pass
    return result


def _status(old_value: str, candidate_value: str, fact_qa: str, canonical_qa: str, source_value: str = "") -> str:
    if not source_value.strip() and not candidate_value.strip():
        return "NO_SOURCE"
    if not candidate_value:
        return "PENDING"
    if old_value.strip() == candidate_value.strip():
        return "KEEP"
    if fact_qa == "PASS" and canonical_qa in {"PASS", "NOT_REQUIRED"}:
        return "PROPOSED"
    return "REVIEW_REQUIRED"


def build_retranslation_batch(
    records: Iterable[Mapping[str, Any]],
    *,
    resolver: Any,
    output_dir: Path,
    limit: int = 10,
    allow_provider: bool = False,
    batch_id: str | None = None,
) -> dict[str, Any]:
    """Generate a field-level retranslation candidate batch, read-only."""
    excluded_statuses = {"OFFLINE", "INACTIVE", "REMOVED", "DELISTED", "DELETED"}
    selected = sorted(
        [
            dict(record)
            for record in records
            if str(record.get("status") or "CURRENT").upper() not in excluded_statuses
        ],
        key=lambda item: str(item.get("sku") or item.get("official_sku") or ""),
    )[: max(0, int(limit))]
    batch_id = batch_id or f"retranslation_{_now_id()}_{len(selected)}sku"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    # Reuse the established shadow report.  It is still read-only and carries
    # Fact QA, Canonical QA, provenance and provider audit artifacts.
    summary = shadow_run(selected, output_dir=output_dir, run_id=batch_id, resolver=resolver, allow_provider=allow_provider)
    units_path = output_dir / "translation_units.csv"
    units = list(csv.DictReader(units_path.open(encoding="utf-8-sig", newline=""))) if units_path.exists() else []
    by_sku = {str(record.get("sku") or record.get("official_sku") or ""): record for record in selected}
    rows: list[dict[str, Any]] = []
    for unit in units:
        sku = str(unit.get("sku") or "")
        record = by_sku.get(sku, {})
        field_name = str(unit.get("field_name") or "")
        if field_name not in CANONICAL_AI_FIELDS:
            continue
        old_value = str(record.get(FIELD_TO_ZH[field_name]) or "")
        candidate_value = str(unit.get("value") or "")
        source_hash = str(unit.get("source_hash") or "")
        identity = _registry_identity(resolver, sku, source_hash, field_name, old_value)
        fact_qa = str(unit.get("fact_qa_status") or unit.get("qa_status") or "NOT_RUN")
        canonical_qa = str(unit.get("canonical_qa_status") or "NOT_RUN")
        source_value = str(record.get(FIELD_TO_ES[field_name]) or "")
        if not source_value.strip() and not candidate_value.strip():
            fact_qa = "NOT_REQUIRED"
            canonical_qa = "NOT_REQUIRED"
        status = _status(old_value, candidate_value, fact_qa, canonical_qa, source_value)
        rows.append({
            "batch_id": batch_id,
            "sku": sku,
            "field_name": field_name,
            "source_es": source_value,
            "source_hash": source_hash,
            "source_version_id": identity["source_version_id"],
            "old_zh": old_value,
            "old_target_hash": value_hash(old_value),
            "old_revision_id": identity["old_revision_id"],
            "approved_revision_id": identity["approved_revision_id"],
            "candidate_zh": candidate_value,
            "candidate_hash": value_hash(candidate_value),
            "resolution_source": str(unit.get("source") or ""),
            "provider": json.loads(unit.get("provenance") or "{}").get("provider", "") if unit.get("provenance") else "",
            "model": json.loads(unit.get("provenance") or "{}").get("model", "") if unit.get("provenance") else "",
            "policy_version": json.loads(unit.get("provenance") or "{}").get("family_policy_version", "") if unit.get("provenance") else "",
            "family_id": str(unit.get("family_id") or ""),
            "family_policy_version": str(unit.get("family_policy_version") or ""),
            "fact_qa_status": fact_qa,
            "canonical_qa_status": canonical_qa,
            "qa_rule_id": str(unit.get("qa_rule_id") or ""),
            "qa_message": str(unit.get("qa_message") or ""),
            "candidate_status": status,
            "owner_decision": "",
            "owner_note": "",
            "apply_status": "NOT_STAGED",
            "conflict_reason": "",
        })
    fields = list(rows[0]) if rows else [
        "batch_id", "sku", "field_name", "source_es", "source_hash", "source_version_id", "old_zh", "old_target_hash", "old_revision_id", "approved_revision_id", "candidate_zh", "candidate_hash", "resolution_source", "provider", "model", "policy_version", "family_id", "family_policy_version", "fact_qa_status", "canonical_qa_status", "qa_rule_id", "qa_message", "candidate_status", "owner_decision", "owner_note", "apply_status", "conflict_reason",
    ]
    with (output_dir / "retranslation_candidates.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)
    review_rows = [row for row in rows if row["candidate_status"] in {"PROPOSED", "REVIEW_REQUIRED"}]
    with (output_dir / "owner_review_queue.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(review_rows)
    manifest = {
        "schema_version": "RETRANSLATION_BATCH_V1",
        "batch_id": batch_id,
        "sku_count": len(selected),
        "translation_unit_count": len(rows),
        "source_snapshot_hash": _snapshot_hash(selected),
        "resolution_source_counts": {},
        "candidate_status_counts": {},
        "owner_review_required": len(review_rows),
        "production_writes": False,
        "master_modified": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "artifacts": ["translation_run_summary.json", "translation_units.csv", "retranslation_candidates.csv", "owner_review_queue.csv", "manifest.json"],
    }
    for row in rows:
        manifest["resolution_source_counts"][row["resolution_source"]] = manifest["resolution_source_counts"].get(row["resolution_source"], 0) + 1
        manifest["candidate_status_counts"][row["candidate_status"]] = manifest["candidate_status_counts"].get(row["candidate_status"], 0) + 1
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**manifest, "output_dir": str(output_dir), "summary": summary}
