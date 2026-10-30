"""Build historical evidence recovery through the existing CommitBundle writer.

Missing ES fields are filled without overwriting existing facts. The reviewed
import range may introduce HISTORICAL_ONLY identities; lifecycle, prices,
Presence and Chinese approval remain outside its authority.
"""
from __future__ import annotations

import hashlib
import json
import csv
import re
from datetime import datetime, timezone
from pathlib import Path

from .contracts import CANONICAL_TO_SOURCE
from .history_audit import ro
from ..database.production import CommitBundle
from ..services.normalization import normalize_official_text
from ..workflow_v2.source_audit import _protected_tokens
from .normalization.structured_details import parse_structured_details

FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")


def _historical_asset(source, cache, *, authorized_service_actors=()):
    """Read an explicitly reviewed asset and exact record; never guess identity."""
    from ..database.immutable_patches import validate_patch_approval_actor
    from ..data_quality.historical.variance import typed_hash
    validate_patch_approval_actor(source.get("reviewed_by"), authorized_service_actors=authorized_service_actors)
    if (source.get("evidence_level") != "A" or source.get("language") != "es"
            or not source.get("review_evidence") or not source.get("source_version")):
        raise ValueError("HISTORICAL_IMPORT_SOURCE_NOT_VERIFIED")
    try:
        reviewed = datetime.fromisoformat(source["reviewed_at"].replace("Z", "+00:00"))
        if reviewed.utcoffset() is None or reviewed > datetime.now(timezone.utc):
            raise ValueError("invalid review time")
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError("HISTORICAL_IMPORT_REVIEW_TIME_INVALID") from exc
    path = Path(source["path"])
    if not path.is_absolute() or not re.fullmatch(r"[a-f0-9]{64}", str(source.get("asset_sha256") or "")):
        raise ValueError("HISTORICAL_IMPORT_ASSET_IDENTITY_REQUIRED")
    kind = source.get("kind")
    if kind not in {"SNAPSHOT_CSV", "VERIFIED_XLSX"}:
        raise ValueError("HISTORICAL_IMPORT_FORMAT_UNSUPPORTED")
    headers = source.get("fields")
    if (not isinstance(headers, dict) or not headers or set(headers) - set(FIELDS)
            or not all(isinstance(h, str) and h for h in headers.values())):
        raise ValueError("HISTORICAL_IMPORT_FIELD_MAP_INVALID")
    key = (str(path), source["asset_sha256"], kind, source.get("sheet"), source.get("sku_header"))
    if key not in cache:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != source["asset_sha256"]:
            raise ValueError("HISTORICAL_IMPORT_ASSET_HASH_CHANGED")
        sku_header = source.get("sku_header")
        if not isinstance(sku_header, str) or not sku_header:
            raise ValueError("HISTORICAL_IMPORT_SKU_COLUMN_REQUIRED")
        if kind == "SNAPSHOT_CSV":
            with path.open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames) or sku_header not in reader.fieldnames:
                    raise ValueError("HISTORICAL_IMPORT_HEADERS_INVALID")
                records = list(reader)
        else:
            from ..exporting.history import _read_workbook
            records = _read_workbook(path, source.get("sheet"), sku_header, reject_duplicate_headers=True)
        indexed = {}
        for record in records:
            sku = str(record.get(sku_header) or "").strip()
            if not sku:
                continue
            if sku in indexed:
                raise ValueError("HISTORICAL_IMPORT_DUPLICATE_SOURCE_SKU")
            indexed[sku] = record
        # Bind parsing to the bytes that were approved, including concurrent
        # edits between the first hash pass and record extraction.
        after = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                after.update(chunk)
        if after.hexdigest() != source["asset_sha256"]:
            raise ValueError("HISTORICAL_IMPORT_ASSET_HASH_CHANGED")
        cache[key] = indexed
    return cache[key], headers, typed_hash


def _build_historical_import(db, candidates, *, run_id, base_commit_id, run_date, authorized_service_actors=()):
    """Pure candidate assembly, also rerun inside the original writer lock."""
    from urllib.parse import urlparse
    from ..services.hashing import localization_field_source_hash
    from ..services.normalization import canonical_id
    from ..data_quality.rules import contains_html_markup, contains_ui_transport_text
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("HISTORICAL_IMPORT_CANDIDATES_REQUIRED")
    products, updates, versions, evidence, blocked = [], [], [], [], []
    grouped, cache, imported = {}, {}, set()
    for (payload,) in db.execute("SELECT e.evidence_json FROM run_evidence e JOIN commit_batches c ON c.run_id=e.run_id WHERE c.status='COMMITTED' AND instr(e.evidence_json,'\"HISTORICAL_FACT_IMPORT\"')>0"):
        record = json.loads(payload)
        if record.get("operation") == "HISTORICAL_FACT_IMPORT":
            imported.update(item["source"]["version_key"] for item in record.get("field_evidence", []))
    for candidate in candidates:
        sku, cid = candidate.get("sku"), candidate.get("canonical_id")
        if not isinstance(sku, str) or not re.fullmatch(r"\d+", sku) or not isinstance(cid, str) or not cid.strip():
            raise ValueError("HISTORICAL_IMPORT_IDENTITY_INVALID")
        source = candidate["source"]
        records, headers, typed_hash = _historical_asset(source, cache, authorized_service_actors=authorized_service_actors)
        raw_record = records.get(sku)
        if raw_record is None or typed_hash(raw_record) != candidate.get("record_hash"):
            raise ValueError("HISTORICAL_IMPORT_RECORD_IDENTITY_CHANGED")
        if any(header not in raw_record for header in headers.values()):
            raise ValueError("HISTORICAL_IMPORT_SOURCE_COLUMN_MISSING")
        group = grouped.setdefault(sku, {"canonical_id": cid, "fields": {}, "urls": set()})
        if group["canonical_id"] != cid:
            raise ValueError("HISTORICAL_IMPORT_CANONICAL_CONFLICT")
        url = raw_record.get(source.get("url_header")) if source.get("url_header") else None
        if url:
            parsed = urlparse(str(url))
            if (parsed.scheme != "https" or parsed.hostname not in {"action.com", "www.action.com"}
                    or not re.fullmatch(r"/es-es/p/" + re.escape(sku) + r"(?:/.*)?", parsed.path)):
                raise ValueError("HISTORICAL_IMPORT_URL_IDENTITY_CONFLICT")
            group["urls"].add(str(url))
        observed = source.get("observed_at")
        if observed:
            if not source.get('observed_at_evidence'):
                raise ValueError('HISTORICAL_IMPORT_ORIGINAL_TIME_EVIDENCE_REQUIRED')
            try:
                stamp = datetime.fromisoformat(str(observed).replace("Z", "+00:00"))
                if len(str(observed)) != 10 and stamp.utcoffset() is None:
                    raise ValueError("timezone required")
                if stamp.date() > datetime.now(timezone.utc).date():
                    raise ValueError("future source observation")
            except (ValueError, TypeError) as exc:
                raise ValueError("HISTORICAL_IMPORT_ORIGINAL_TIME_INVALID") from exc
        for field, header in headers.items():
            raw = raw_record.get(header)
            if raw in (None, ""):
                blocked.append({"sku": sku, "field": field, "reason": "SOURCE_UNAVAILABLE"})
                continue
            if not isinstance(raw, str) or re.search(r"[\u3400-\u9fff]", raw) or contains_html_markup(raw) or contains_ui_transport_text(raw):
                blocked.append({"sku": sku, "field": field, "reason": "SOURCE_LANGUAGE_OR_TYPE_REVIEW"})
                continue
            normalized = normalize_official_text(raw, field=field) if field in {"spec", "description", "details"} else raw
            if (not normalized or _protected_tokens(raw) != _protected_tokens(normalized)
                    or (field == "details" and [p.key for p in parse_structured_details(raw)] != [p.key for p in parse_structured_details(normalized)])):
                blocked.append({"sku": sku, "field": field, "reason": "SOURCE_NORMALIZATION_FACT_CHANGED"})
                continue
            version_key = typed_hash([source["asset_sha256"], source.get("sheet"), candidate["record_hash"], field, source["source_version"]])
            item = {"sku": sku, "field": field, "raw": raw, "normalized": normalized,
                "status": "VERIFIED", "source": {"text": raw,
                    "reference": str(source["path"]) + "::" + str(source.get("sheet") or "CSV") + "::SKU=" + sku,
                    "file_hash": source["asset_sha256"], "record_hash": candidate["record_hash"],
                    "field_hash": localization_field_source_hash({CANONICAL_TO_SOURCE[field]: raw}, field),
                    "observed_at": observed, "source_version": source["source_version"], "version_key": version_key,
                    "observed_at_evidence": source.get('observed_at_evidence'),
                    "reviewed_by": source["reviewed_by"], "reviewed_at": source["reviewed_at"], "review_evidence": source["review_evidence"]}}
            if version_key in {old["source"]["version_key"] for old in evidence}:
                raise ValueError("HISTORICAL_IMPORT_DUPLICATE_SOURCE_FIELD")
            evidence.append(item)
            group["fields"].setdefault(field, []).append(item)
            if version_key not in imported:
                versions.append({"sku": sku, "run_id": run_id, "source_name": "historical_import",
                    "observed_at": observed, "facts": {field: {"raw": raw, "normalized": normalized}}})
    committed = []
    for sku, group in sorted(grouped.items()):
        existing = db.execute("SELECT canonical_id,status FROM products WHERE official_sku=?", (sku,)).fetchone()
        collision = db.execute("SELECT official_sku FROM products WHERE canonical_id=?", (group["canonical_id"],)).fetchone()
        if (existing and existing[0] != group["canonical_id"]) or (collision and collision[0] != sku):
            raise ValueError("HISTORICAL_IMPORT_CANONICAL_CONFLICT")
        if not existing and group['canonical_id'] != canonical_id(sku):
            raise ValueError('HISTORICAL_IMPORT_NEW_CANONICAL_INVALID')
        row = db.execute("SELECT * FROM product_localizations WHERE official_sku=? AND language='es'", (sku,)).fetchone()
        values = dict(row) if row else {field: None for field in FIELDS}
        # A legacy product-level title is still a valid existing official fact.
        product_name = db.execute("SELECT name_es FROM products WHERE official_sku=?", (sku,)).fetchone()[0] if existing else None
        changed = []
        for field, items in group["fields"].items():
            distinct = {item["normalized"] for item in items}
            protected = product_name if field == 'name' and values.get(field) in (None, '') else values.get(field)
            if protected not in (None, ""):
                if any(item["normalized"] != protected for item in items):
                    blocked.append({"sku": sku, "field": field, "reason": "EXISTING_FACT_PRESERVED_SOURCE_VARIANCE"})
                continue
            if len(distinct) != 1:
                blocked.append({"sku": sku, "field": field, "reason": "SOURCE_VERSION_CONFLICT_KEEP_BOTH"})
                continue
            values[field] = items[0]["normalized"]
            changed.append(field)
            committed.append({"sku": sku, "field": field, "value_hash": typed_hash(values[field]),
                              "source_version_keys": sorted(item["source"]["version_key"] for item in items)})
        if not existing and group["fields"]:
            products.append({"sku": sku, "canonical_id": group["canonical_id"], "status": "HISTORICAL_ONLY",
                "name_es": values.get("name"), "product_url": next(iter(group["urls"])) if len(group["urls"]) == 1 else None,
                "first_seen_at": None, "last_seen_at": None, "last_checked_at": None})
        if changed:
            for key in ("updated_at", "last_commit_id"):
                values.pop(key, None)
            updates.append({**values, "sku": sku, "language": "es", "source": "HISTORICAL_VERIFIED_IMPORT",
                "review_status": "VERIFIED", "freshness_status": "CURRENT",
                **{field + "_source": "HISTORY_RETAINED:EXISTING_FACT" for field in FIELDS if field not in changed}})
    return CommitBundle(run_id=run_id, observation_date=run_date, qa_state="PASS", base_commit_id=base_commit_id,
        current_products=tuple(products), localization_updates=tuple(updates), source_fact_versions=tuple(versions),
        run_record={"operation": "HISTORICAL_FACT_IMPORT", "commit_scope": "HISTORICAL_FACTS_ONLY",
            "historical_candidates": candidates, "field_evidence": evidence, "historical_committed_fields": committed,
            "blocked": blocked, "presence_authority": False, "source_unavailable_is_not_official_empty": True})


def build_historical_import_bundle(database, candidates, *, run_id, base_commit_id, run_date, authorized_service_actors=()):
    """Preview trusted history using the same writer; no current/price inference."""
    frozen = json.loads(json.dumps(candidates, ensure_ascii=False, allow_nan=False))
    if not isinstance(frozen, list) or not frozen:
        raise ValueError("HISTORICAL_IMPORT_CANDIDATES_REQUIRED")
    with ro(database) as db:
        bundle = _build_historical_import(db, frozen, run_id=run_id, base_commit_id=base_commit_id, run_date=run_date,
                                         authorized_service_actors=authorized_service_actors)
    return bundle, {"new_identities": len(bundle.current_products),
        "filled_fields": len(bundle.run_record["historical_committed_fields"]),
        "source_versions": len(bundle.source_fact_versions), "blocked": bundle.run_record["blocked"]}


def validate_historical_import_commit(db, bundle, *, authorized_service_actors=()):
    """Rebuild exact authorized scope inside the one existing transaction."""
    operation = bundle.run_record.get("operation")
    scope = bundle.run_record.get("commit_scope")
    if operation != "HISTORICAL_FACT_IMPORT" and scope != "HISTORICAL_FACTS_ONLY":
        return
    if operation != "HISTORICAL_FACT_IMPORT" or scope != "HISTORICAL_FACTS_ONLY" or bundle.base_commit_id is None:
        raise ValueError("HISTORICAL_IMPORT_SCOPE_INVALID")
    expected = _build_historical_import(db, bundle.run_record.get("historical_candidates") or [],
        run_id=bundle.run_id, base_commit_id=bundle.base_commit_id, run_date=bundle.observation_date,
        authorized_service_actors=authorized_service_actors)
    if bundle.resolved_hash() != expected.resolved_hash():
        raise ValueError("HISTORICAL_IMPORT_SCOPE_OR_FACT_CHANGED")


def validate_historical_import_resume(bundle, *, authorized_service_actors=()):
    """An exact committed Resume never reapplies facts, but checks its assets."""
    if bundle.run_record.get("operation") != "HISTORICAL_FACT_IMPORT":
        return
    cache = {}
    for candidate in bundle.run_record["historical_candidates"]:
        records, _, typed_hash = _historical_asset(candidate["source"], cache, authorized_service_actors=authorized_service_actors)
        if typed_hash(records.get(candidate["sku"])) != candidate["record_hash"]:
            raise ValueError("HISTORICAL_IMPORT_RECORD_IDENTITY_CHANGED")


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
