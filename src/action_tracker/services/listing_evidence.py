"""Committed, same-layer Listing evidence; canonical Detail is a separate source."""
from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Mapping

CONTRACT = "LISTING_FIELD_EVIDENCE_V1"
TEXT_FIELDS = ("name_es", "cat1_es", "spec_es", "product_url", "image_url")
LISTING_CANONICAL_FIELDS = (*TEXT_FIELDS, "unit_price")
CANONICAL_FIELDS = (*LISTING_CANONICAL_FIELDS, "cat2_es", "desc_es", "details_es")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _digest(rows: list[dict]) -> str:
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def _validate_row(row: Any, *, require_categories: bool) -> None:
    if (not isinstance(row, Mapping) or not isinstance(row.get("sku"), str)
            or not row["sku"].isdigit()
            or any(field not in row or not isinstance(row[field], str) for field in TEXT_FIELDS)):
        raise ValueError("LISTING_EVIDENCE_FIELD_SCHEMA_INVALID")
    if require_categories:
        categories = row.get("_listing_categories")
        if not isinstance(categories, list) or any(not isinstance(value, str) for value in categories):
            raise ValueError("LISTING_EVIDENCE_CATEGORY_SCHEMA_INVALID")
        pending = row.get("_canonical_confirmation_pending", [])
        if not isinstance(pending, list) or any(value not in TEXT_FIELDS for value in pending):
            raise ValueError("LISTING_EVIDENCE_PENDING_SCHEMA_INVALID")


def listing_evidence(records: Mapping[str, Mapping[str, Any]], *, run_id: str,
                     business_date: str, canonical_records: Mapping[str, Mapping[str, Any]] | None = None,
                     completed_detail_skus: set[str] | None = None,
                     previous: Mapping[str, Mapping[str, Any]] | None = None) -> dict:
    rows = [{"sku": str(sku), **{f: _text(record.get(f)) for f in TEXT_FIELDS},
             "_listing_categories": sorted(set(record.get("_listing_categories") or
                                                [record.get("cat1_es") or ""]))}
            for sku, record in sorted(records.items())]
    for row in rows:
        sku = row["sku"]
        prior = (previous or {}).get(sku) or {}
        observed = records[sku]
        confirmed = (sku in (completed_detail_skus or set()) or
            (prior.get("_listing_evidence_confidence") == "COMMITTED_HASH_BOUND"
             and not prior.get("_canonical_confirmation_pending")
             and not listing_differences(observed, prior)))
        row["_canonical_confirmation_pending"] = []
        if canonical_records is not None and not confirmed:
            canonical = canonical_records.get(sku) or {}
            row["_canonical_confirmation_pending"] = [field for field in TEXT_FIELDS
                if field in observed and observed[field] is not None
                and _text(observed[field]) != _text(canonical.get(field))]
        _validate_row(row, require_categories=True)
    return {"contract": CONTRACT, "run_id": run_id, "business_date": business_date,
            "rows": rows, "rows_hash": _digest(rows)}


def listing_differences(current: Mapping[str, Any], previous: Mapping[str, Any]) -> tuple[str, ...]:
    """Compare observed card fields to card fields, without semantic rewriting."""
    changed = []
    for field in TEXT_FIELDS:
        if field not in previous:
            continue
        if field == "cat1_es" and previous.get("_listing_categories") and current.get("_listing_categories"):
            differs = set(previous["_listing_categories"]) != set(current["_listing_categories"])
        else:
            differs = _text(current.get(field)) != _text(previous.get(field))
        if field in current and current[field] is not None and differs:
            changed.append(field)
    return tuple(changed)


def merge_listing_fields(record: dict, light: Mapping[str, Any], verified: dict[str, str]) -> None:
    """Listing supplements empty canonical fields; verified Detail owns its values.

    Conflicting nonempty card text is evidence for Detail confirmation, not
    authority to overwrite the canonical Detail/history projection.
    """
    for field in LISTING_CANONICAL_FIELDS:
        value = light.get(field)
        if value in (None, ""):
            continue
        if verified.get(field) == "DETAIL_CURRENT_RUN":
            continue
        if field == "unit_price" or record.get(field) in (None, ""):
            record[field] = value
            verified[field] = "LISTING_CURRENT_RUN"
        elif _text(record.get(field)) == _text(value):
            verified.setdefault(field, "LISTING_CURRENT_RUN")


def matching_listing_fields(record: Mapping[str, Any], light: Mapping[str, Any]) -> dict[str, str]:
    """Do not claim a Listing value supplied a different canonical value."""
    verified = {f: "LISTING_CURRENT_RUN" for f, value in light.items()
                if value not in (None, "") and record.get(f) == value}
    if (light.get("_price_observation_complete") is True
            and record.get("current_price") == light.get("current_price")
            and record.get("original_price") == light.get("original_price")):
        verified["original_price"] = "LISTING_CURRENT_RUN"
    return verified


def _legacy_snapshot(directory: Path, *, run_id: str, business_date: str) -> list[dict]:
    manifest = json.loads((directory / "run_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("run_id") != run_id or manifest.get("observation_date") != business_date:
        raise ValueError("LISTING_SNAPSHOT_IDENTITY_MISMATCH")
    raw = json.loads((directory / "listing_raw.json").read_text(encoding="utf-8"))
    cards: dict[str, list[dict]] = {}
    for category, values in raw.items():
        if category in {"nuevo", "promocion-semanal"}:
            continue
        for value in values:
            _validate_row(value, require_categories=False)
            cards.setdefault(str(value["sku"]), []).append(value)
    with (directory / "listing_products.csv").open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    for row in rows:
        _validate_row(row, require_categories=False)
        candidates = cards.get(str(row["sku"]), [])
        if not any(all(_text(row.get(f)) == _text(card.get(f)) for f in TEXT_FIELDS)
                   for card in candidates):
            raise ValueError("LISTING_SNAPSHOT_CARD_PARITY_FAILED")
        row["_listing_categories"] = sorted({_text(card.get("cat1_es")) for card in candidates})
    return rows


def committed_listing_baseline(path: Path, *, expected_base_commit_id: str | None,
                               snapshots_root: Path | None = None, max_runs: int = 5) -> dict[str, dict]:
    """Read verified prior observations without modifying SQLite or snapshots.

    Verify existing full-snapshot hashes when present. Legacy snapshots whose
    post-commit report changed that digest remain explicitly LEGACY_MATCHED;
    run/date/SKU/card parity does not upgrade them to immutable evidence.
    Future runs bind finite Listing evidence in committed run_evidence JSON.
    """
    result = {}
    with sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True) as db:
        head = db.execute("SELECT commit_id FROM commit_batches WHERE status='COMMITTED' ORDER BY committed_at DESC,commit_id DESC LIMIT 1").fetchone()
        if head is None or str(head[0]) != str(expected_base_commit_id or ""):
            if head is None and not expected_base_commit_id:
                return result
            raise ValueError("LISTING_BASELINE_HEAD_CHANGED")
        runs = db.execute("""SELECT c.commit_id,c.run_id,r.run_date,e.snapshot_path,e.snapshot_hash,e.evidence_json
            FROM commit_batches c JOIN runs r USING(run_id) JOIN run_evidence e USING(run_id)
            WHERE c.status='COMMITTED' AND r.status='COMMITTED' AND r.qa_state IN ('PASS','PASS_PRESENCE_ONLY')
            AND r.dry_run=0 AND EXISTS(SELECT 1 FROM observations o WHERE o.run_id=c.run_id
                AND o.listing_present=1 AND o.observation_complete=1)
            ORDER BY c.committed_at DESC,c.commit_id DESC LIMIT ?""", (max(0, int(max_runs)),)).fetchall()
        for commit_id, run_id, business_date, stored_path, snapshot_hash, payload in runs:
            present = {str(r[0]) for r in db.execute("SELECT official_sku FROM observations WHERE run_id=? AND listing_present=1 AND observation_complete=1", (run_id,))}
            try:
                evidence = json.loads(payload or "{}").get("listing_field_evidence")
                if evidence is not None:
                    rows = evidence["rows"]
                    if not isinstance(rows, list):
                        raise ValueError("LISTING_EVIDENCE_ROWS_INVALID")
                    if (evidence.get("contract") != CONTRACT or evidence.get("run_id") != run_id
                            or evidence.get("business_date") != business_date or evidence.get("rows_hash") != _digest(rows)):
                        raise ValueError("LISTING_COMMITTED_EVIDENCE_INVALID")
                    for row in rows:
                        _validate_row(row, require_categories=True)
                    evidence_ref = f"run_evidence:{run_id}"
                    confidence = "COMMITTED_HASH_BOUND"
                    file_hashes = {}
                else:
                    local = Path(snapshots_root) / business_date / run_id if snapshots_root else None
                    if not (local and local.exists()) and not stored_path:
                        raise ValueError("LISTING_SNAPSHOT_PATH_MISSING")
                    directory = local if local and local.exists() else Path(stored_path)
                    rows = _legacy_snapshot(directory, run_id=run_id, business_date=business_date)
                    evidence_ref = str(directory / "listing_products.csv")
                    from ..database.integration import snapshot_digest
                    confidence = ("COMMITTED_HASH_BOUND" if snapshot_hash and
                                  snapshot_digest(directory) == snapshot_hash else "LEGACY_MATCHED")
                    file_hashes = {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
                                   for name in ("run_manifest.json", "listing_raw.json", "listing_products.csv")}
                identities = [str(r["sku"]) for r in rows]
                if len(identities) != len(set(identities)) or any(not sku for sku in identities):
                    raise ValueError("LISTING_EVIDENCE_DUPLICATE_IDENTITY")
                for row in rows:
                    sku = str(row["sku"])
                    if sku in present and sku not in result:
                        result[sku] = {**row, "_listing_source_run_id": run_id,
                            "_listing_source_date": business_date, "_listing_source_commit_id": commit_id,
                            "_listing_evidence_ref": evidence_ref, "_listing_evidence_hash": _digest([row]),
                            "_listing_evidence_confidence": confidence,
                            "_listing_snapshot_hash": snapshot_hash, "_listing_file_sha256": file_hashes}
                # A complete Listing observation with no corresponding card
                # is a coverage gap, not permission to use an older card.
                for sku in present - set(identities):
                    result.setdefault(sku, {"sku": sku, "_listing_source_run_id": run_id,
                        "_listing_source_date": business_date, "_listing_source_commit_id": commit_id,
                        "_listing_evidence_confidence": "UNKNOWN"})
            except (OSError, ValueError, KeyError, TypeError):
                # No fabricated change: the planner schedules bounded unknown
                # evidence probes and preserves nonempty canonical history.
                for sku in present:
                    result.setdefault(sku, {"sku": sku, "_listing_source_run_id": run_id,
                        "_listing_source_date": business_date, "_listing_source_commit_id": commit_id,
                        "_listing_evidence_confidence": "UNKNOWN"})
    return result
