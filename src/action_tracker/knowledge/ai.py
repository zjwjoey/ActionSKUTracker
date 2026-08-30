"""Provider-neutral incremental AI candidate runner.

The runner never writes product_localizations.  Providers are injected by the
caller, which keeps CI offline and prevents credentials from entering the repo.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from .contracts import KNOWLEDGE_FIELDS, source_hash
from .validator import validate_candidate


@dataclass(frozen=True)
class ProviderResult:
    fields: Mapping[str, str]
    confidence: float | None = None


def candidate_cache_key(sku: str, digest: str, prompt_version: str, requested_fields: Iterable[str]) -> str:
    payload = [str(sku), str(digest), str(prompt_version), sorted(set(requested_fields))]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def run_candidates(queue_rows: Iterable[Mapping[str, Any]], records: Mapping[str, Mapping[str, Any]],
                   provider: Callable[[Mapping[str, Any], tuple[str, ...]], ProviderResult], *,
                   prompt_version: str = "translation_v1", max_retries: int = 3) -> list[dict[str, Any]]:
    """Process pending rows with bounded retry; output remains candidate-only."""
    output: list[dict[str, Any]] = []
    for row in queue_rows:
        if str(row.get("status") or "PENDING").upper() not in {"PENDING", "RETRY"}:
            continue
        sku = str(row.get("sku") or row.get("official_sku") or "")
        record = records.get(sku)
        if not record or str(row.get("source_hash") or "") != source_hash(record):
            output.append({**dict(row), "status": "BLOCKED", "failure_reason": "SOURCE_HASH_MISMATCH"})
            continue
        fields = tuple(f for f in row.get("requested_fields") or () if f in KNOWLEDGE_FIELDS)
        error = ""
        for attempt in range(max(1, max_retries)):
            try:
                result = provider(record, fields)
                candidate = {"candidate_id": candidate_cache_key(sku, row["source_hash"], prompt_version, fields),
                             "queue_id": row["queue_id"], "sku": sku, "source_hash": row["source_hash"],
                             "fields": dict(result.fields), "confidence": result.confidence,
                             "prompt_version": prompt_version, "model_provider": "injected", "model_name": "injected",
                             "retry_count": attempt}
                validation = validate_candidate(candidate, record)
                candidate.update({"validation_status": "PASS" if validation.ok else "FAIL",
                                  "validation_reasons": validation.reasons,
                                  "status": "CANDIDATE" if validation.ok else "REVIEW_REQUIRED"})
                output.append(candidate)
                break
            except Exception as exc:  # provider failures are isolated from product commit
                error = type(exc).__name__
        else:
            output.append({**dict(row), "status": "FAILED", "failure_reason": error or "PROVIDER_ERROR",
                           "retry_count": max(1, max_retries)})
    return output
