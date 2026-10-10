"""Build historical evidence recovery through the existing CommitBundle writer.

Only missing ES projection fields are filled. Existing Spanish, Chinese,
products, lifecycle, prices and observations are never part of this bundle.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .contracts import CANONICAL_TO_SOURCE
from .history_audit import ro
from ..database.production import CommitBundle
from ..services.normalization import normalize_official_text
from ..workflow_v2.source_audit import _protected_tokens
from .normalization.structured_details import parse_structured_details

FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")


def build_historical_source_bundle(database, rows, *, run_id, base_commit_id, run_date):
    grouped = {}; seen = set(); blocked = []; evidence = []; updates = []
    for row in rows:
        key = (row["sku"], row["field"])
        if key in seen or row["field"] not in FIELDS:
            raise ValueError("HISTORY_DUPLICATE_OR_INVALID_FIELD")
        seen.add(key); grouped.setdefault(row["sku"], []).append(row)
    file_hashes = {}
    with ro(database) as db:
        for sku, fields in sorted(grouped.items()):
            product = db.execute("SELECT status FROM products WHERE official_sku=?", (sku,)).fetchone()
            if not product or product[0] not in {"HISTORICAL", "ABSENT", "MISSING", "OFFLINE"}:
                raise ValueError("HISTORY_NON_CURRENT_SCOPE_REQUIRED")
            current = db.execute("SELECT name,cat1,cat2,spec,description,details FROM product_localizations WHERE official_sku=? AND language='es'", (sku,)).fetchone()
            original = dict(zip(FIELDS, current or (None,) * 6))
            values = dict(original); changed = []
            for row in fields:
                field = row["field"]; selected = row["evidence"].get("selected")
                if values[field] not in (None, ""):
                    continue
                if row["evidence"]["status"] != "EVIDENCE_AVAILABLE" or not selected:
                    blocked.append({"sku": sku, "field": field, "reason": row["evidence"]["status"]})
                    continue
                reference = str(selected["reference"]); artifact = reference.split("::")[0]
                if not artifact.startswith("PRIMARY:"):
                    if artifact not in file_hashes:
                        file_hashes[artifact] = hashlib.sha256(Path(artifact).read_bytes()).hexdigest()
                    if file_hashes[artifact] != selected["file_hash"]:
                        raise ValueError("HISTORY_SOURCE_ARTIFACT_CHANGED")
                raw = selected["text"]
                normalized = normalize_official_text(raw, field=field) if field in {"spec", "description", "details"} else raw
                if not normalized or _protected_tokens(raw) != _protected_tokens(normalized):
                    blocked.append({"sku": sku, "field": field, "reason": "SOURCE_NORMALIZATION_FACT_CHANGED"})
                    continue
                if field == "details":
                    explicit = [p.key for p in parse_structured_details(raw)]
                    normalized_keys = iter(p.key for p in parse_structured_details(normalized))
                    if any(not any(key == candidate for candidate in normalized_keys) for key in explicit):
                        blocked.append({"sku": sku, "field": field, "reason": "SOURCE_DETAIL_KEY_CHANGED"})
                        continue
                values[field] = normalized; changed.append(field)
                evidence.append({"sku": sku, "field": field, "raw": raw, "normalized": normalized,
                                 "source": selected, "status": "VERIFIED"})
            if changed:
                # The standard ES writer normalizes all three text fields.
                # Reject an incidental change to an unrelated existing fact.
                for field in ("spec", "description", "details"):
                    if field not in changed and normalize_official_text(values[field], field=field) != values[field]:
                        raise ValueError("HISTORY_UNRELATED_NORMALIZATION_CHANGE")
                updates.append({"sku": sku, "language": "es", **values,
                                "source": "HISTORICAL_EVIDENCE_RECOVERY", "review_status": "VERIFIED",
                                "freshness_status": "CURRENT"})
    bundle = CommitBundle(run_id=run_id, observation_date=run_date, qa_state="PASS",
        base_commit_id=base_commit_id, localization_updates=tuple(updates),
        run_record={"operation": "HISTORICAL_SOURCE_RECOVERY", "field_evidence": evidence,
                    "source_unavailable_is_not_official_empty": True,
                    "source_field_count": len(evidence), "blocked": blocked})
    return bundle, {"recovered_fields": len(evidence), "recovered_skus": len(updates),
                    "blocked_fields": blocked, "chinese_changed": 0}


def build_historical_normalization_correction_bundle(database, *, run_id, base_commit_id, run_date):
    """Reproject only proven historical restorations from their retained raw source.

    Current listings and pre-existing facts cannot enter this correction. A
    changed current value fails closed rather than overwriting a later version.
    """
    updates = []; evidence = []; file_hashes = {}; restored = {}
    with ro(database) as db:
        for (payload,) in db.execute("SELECT evidence_json FROM run_evidence"):
            record = json.loads(payload or "{}")
            if record.get("operation") != "HISTORICAL_SOURCE_RECOVERY":
                continue
            for item in record.get("field_evidence", []):
                if item.get("field") == "details" and item.get("status") == "VERIFIED":
                    restored[item["sku"]] = item
        for sku, item in sorted(restored.items()):
            raw = item["raw"]; normalized = normalize_official_text(raw, field="details")
            if normalized == item["normalized"]:
                continue
            status = db.execute("SELECT status FROM products WHERE official_sku=?", (sku,)).fetchone()
            if not status or status[0] not in {"HISTORICAL", "ABSENT", "MISSING", "OFFLINE"}:
                raise ValueError("HISTORY_NON_CURRENT_SCOPE_REQUIRED")
            current = db.execute("SELECT name,cat1,cat2,spec,description,details FROM product_localizations WHERE official_sku=? AND language='es'", (sku,)).fetchone()
            if current is None:
                raise ValueError("HISTORY_RESTORED_SOURCE_MISSING")
            if current[5] == normalized:
                continue
            if current[5] != item["normalized"]:
                raise ValueError("HISTORY_RESTORED_SOURCE_CHANGED_REVIEW_REQUIRED")
            source = item["source"]; artifact = str(source["reference"]).split("::")[0]
            if not artifact.startswith("PRIMARY:"):
                if artifact not in file_hashes:
                    file_hashes[artifact] = hashlib.sha256(Path(artifact).read_bytes()).hexdigest()
                if file_hashes[artifact] != source["file_hash"]:
                    raise ValueError("HISTORY_SOURCE_ARTIFACT_CHANGED")
            if _protected_tokens(raw) != _protected_tokens(normalized):
                raise ValueError("HISTORY_CORRECTION_FACT_CHANGED")
            if [p.key for p in parse_structured_details(raw)] != [p.key for p in parse_structured_details(normalized)]:
                raise ValueError("HISTORY_CORRECTION_DETAIL_KEY_CHANGED")
            values = dict(zip(FIELDS, current)); values["details"] = normalized
            updates.append({"sku": sku, "language": "es", **values, "source": "HISTORICAL_EVIDENCE_RECOVERY_CORRECTION", "review_status": "VERIFIED", "freshness_status": "CURRENT"})
            evidence.append({"sku": sku, "field": "details", "before": current[5], "after": normalized, "raw": raw, "source": source})
    bundle = CommitBundle(run_id=run_id, observation_date=run_date, qa_state="PASS", base_commit_id=base_commit_id,
        localization_updates=tuple(updates), run_record={"operation": "HISTORICAL_NORMALIZATION_CORRECTION", "field_evidence": evidence})
    return bundle, {"corrected_fields": len(evidence), "corrected_skus": len(updates), "chinese_changed": 0, "evidence": evidence}
