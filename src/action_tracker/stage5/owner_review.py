"""Read-only binding of the signed Stage 5 review to immutable candidates."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import openpyxl

from .pipeline import sha256_file
from ..translation.model_guard import validate_model_output


ACCEPTED = frozenset({"ACCEPT_AS_IS", "ACCEPT_WITH_MINOR_EDIT"})
ISOLATED = frozenset({"REQUIRES_MAJOR_EDIT", "AMBIGUOUS", "REJECT"})


def load_signed_rows(workbook_path: Path, expected_sha256: str) -> list[dict[str, Any]]:
    if sha256_file(workbook_path) != expected_sha256:
        raise ValueError("OWNER_WORKBOOK_HASH_CHANGED")
    book = openpyxl.load_workbook(workbook_path, read_only=True, data_only=True)
    try:
        iterator = book["Stage5_103"].values
        headers = next(iterator)
        rows = [dict(zip(headers, values)) for values in iterator]
    finally:
        book.close()
    if len(rows) != 103 or len({str(row.get("review_id")) for row in rows}) != len(rows):
        raise ValueError("OWNER_REVIEW_CARDINALITY_INVALID")
    return rows


def load_replayed_candidates(root: Path) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    candidates: dict[str, dict[str, Any]] = {}
    manifests: dict[str, str] = {}
    for batch in ("01", "02", "03"):
        folder = root / f"batch_{batch}"
        manifest_path = folder / f"stage5_batch_{batch}_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        candidate_entry = manifest["artifacts"]["candidates"]
        candidate_path = Path(candidate_entry["path"])
        if sha256_file(candidate_path) != candidate_entry["sha256"]:
            raise ValueError(f"CANDIDATE_HASH_CHANGED:{batch}")
        manifests[batch] = sha256_file(manifest_path)
        for line in candidate_path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            candidate_id = row["candidate_id"]
            if candidate_id in candidates:
                raise ValueError("DUPLICATE_CANDIDATE_ID")
            candidates[candidate_id] = row
    return candidates, manifests


def certify_owner_review(
    rows: list[dict[str, Any]], candidates: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Validate every signed disposition without mutating owner decisions.

    A signed minor edit always uses the owner's final value, never the raw
    model output.  A rejected Guard value cannot be promoted by this function.
    """
    counts: Counter[str] = Counter()
    approved: list[dict[str, Any]] = []
    isolated: list[dict[str, Any]] = []
    issues: list[str] = []
    for row in rows:
        sku = str(row.get("sku") or "").strip()
        field = str(row.get("field") or "").strip()
        candidate_id = str(row.get("candidate_id") or "").strip()
        review_id = str(row.get("review_id") or "").strip()
        disposition = str(row.get("owner_disposition") or "").strip()
        counts[disposition] += 1
        candidate = candidates.get(candidate_id)
        expected_review_id = hashlib.sha256(f"stage5-review-v1|{candidate_id}".encode()).hexdigest()
        if (
            candidate is None or candidate.get("sku") != sku or candidate.get("source_field") != field
            or review_id != expected_review_id or not row.get("owner_reviewer")
            or not row.get("owner_reviewed_at")
            or candidate.get("source_spanish_value") != row.get("spanish_source")
        ):
            issues.append(f"OWNER_CANDIDATE_LINEAGE_INVALID:{sku}:{field}")
            continue
        if disposition in ACCEPTED:
            final = row.get("owner_final_candidate")
            if not isinstance(final, str) or not final.strip():
                issues.append(f"OWNER_FINAL_VALUE_MISSING:{sku}:{field}")
                continue
            result = validate_model_output(
                {field: candidate["source_spanish_value"]}, {field: final}, expected_fields=[field],
            )
            if not result.accepted:
                issues.append(f"OWNER_FINAL_GUARD_REJECT:{sku}:{field}:{','.join(result.reasons)}")
                continue
            approved.append({"owner": row, "candidate": candidate, "reviewed_value": final})
        elif disposition in ISOLATED:
            isolated.append({"sku": sku, "field": field, "candidate_id": candidate_id, "disposition": disposition})
        else:
            issues.append(f"OWNER_DISPOSITION_INVALID:{sku}:{field}:{disposition}")
    return {
        "counts": dict(sorted(counts.items())), "approved": approved,
        "isolated": isolated, "issues": issues,
    }
