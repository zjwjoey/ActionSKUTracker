"""Incremental translation queue builder with stable deduplication."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

from .contracts import KNOWLEDGE_FIELDS, Resolution, source_hash


def _queue_id(sku: str, digest: str, fields: tuple[str, ...]) -> str:
    raw = json.dumps([sku, digest, fields], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_queue(
    records: Iterable[Mapping[str, Any]],
    *,
    localizations: Mapping[str, Mapping[str, Any]] | None = None,
    resolutions: Mapping[str, Resolution] | None = None,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    """Build P0--P3 queue rows; SOURCE_BLOCKED is explicitly excluded."""
    existing = localizations or {}
    output: dict[str, dict[str, Any]] = {}
    for record in records:
        sku = str(record.get("sku") or record.get("official_sku") or "").strip()
        if not sku:
            continue
        resolution = (resolutions or {}).get(sku)
        digest = resolution.source_hash if resolution else source_hash(record)
        source_quality = str(record.get("source_quality") or record.get("source_quality_status") or "OK").upper()
        if source_quality in {"SOURCE_BLOCKED", "SOURCE_DAMAGED", "SOURCE_POLLUTED", "SOURCE_UNTRUSTED"}:
            continue
        if resolution and resolution.readiness == "SOURCE_BLOCKED":
            continue
        loc = existing.get(sku, {})
        missing = tuple(field for field in KNOWLEDGE_FIELDS if not str(loc.get(field) or "").strip())
        # An empty hash means freshness is unknown, not "unchanged".  This
        # queues legacy localization rows so their provenance can be rebuilt.
        existing_hash = str(loc.get("source_hash") or "")
        changed = not existing_hash or existing_hash != digest
        is_new = bool(record.get("is_new") or record.get("new") or record.get("status") == "NEW")
        if not (is_new or changed or missing or (resolution and resolution.readiness == "REVIEW_REQUIRED")):
            continue
        requested = tuple(field for field in KNOWLEDGE_FIELDS if field in missing or changed or is_new)
        if not requested:
            requested = KNOWLEDGE_FIELDS
        priority = "P0" if is_new and any(field in missing for field in ("name", "cat1", "cat2")) else ("P1" if changed else ("P2" if any(field in missing for field in ("spec", "description", "details")) else "P3"))
        reason = "NEW" if is_new else ("SOURCE_HASH_CHANGED" if changed else "MISSING_LOCALIZATION")
        qid = _queue_id(sku, digest, requested)
        output[qid] = {
            "queue_id": qid, "sku": sku, "language": "zh", "source_hash": digest,
            "requested_fields": requested, "reason": reason, "priority": priority,
            "status": "PENDING", "retry_count": 0, "run_id": run_id,
        }
    return sorted(output.values(), key=lambda row: (row["priority"], row["sku"], row["queue_id"]))
