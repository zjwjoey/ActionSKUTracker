"""Source-bound import of explicit owner decisions for Workflow V2 reviews.

The semantic review CSV is evidence only.  This adapter makes a separate,
human-authored decision file the sole authority for changing a registry
revision's review status.  It never updates SQLite statuses directly: all
mutations go through :class:`LocalizationRegistry`'s append-only APIs.
"""
from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..localization.registry.repository import LocalizationRegistry


_ACCEPTED_DECISIONS = frozenset({"APPROVE", "EDIT_AND_APPROVE"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: object) -> str:
    return str(value or "").strip()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return [{key: _text(value) for key, value in row.items()} for row in csv.DictReader(handle)]


def _current_revision(path: Path, sku: str, field_name: str) -> dict[str, Any] | None:
    canonical = {
        "name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es",
        "spec": "spec_es", "description": "desc_es", "details": "details_es",
    }.get(field_name, field_name)
    with sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            """SELECT r.revision_id,r.target_text,r.source_hash,r.qa_status,
                      r.canonical_qa_status,r.review_status,r.approved_by,
                      u.field_name,u.freshness_status
                 FROM translation_revisions r
                 JOIN translation_units u ON u.current_revision_id=r.revision_id
                 JOIN translation_source_versions s ON s.source_version_id=u.source_version_id
                WHERE s.official_sku=? AND u.field_name IN (?,?)
                ORDER BY r.created_at DESC LIMIT 1""",
            (sku, field_name, canonical),
        ).fetchone()
    return dict(row) if row else None


def apply_owner_decisions(
    registry: LocalizationRegistry,
    review_rows: Iterable[Mapping[str, object]],
    owner_decisions: Iterable[Mapping[str, object]],
    *,
    actor: str,
) -> dict[str, object]:
    """Append and approve source-bound owner decisions.

    Every decision must name a row from the review artifact and repeat its
    source hash and parent revision id.  This prevents a stale CSV or a SKU
    collision from approving a different field.  An unchanged target is
    approved in place; an edited target becomes a new, QA-pass manual
    revision whose parent is the reviewed candidate.
    """
    if not _text(actor).startswith("human:"):
        raise ValueError("OWNER_APPROVAL_ACTOR_REQUIRED")
    reviews: dict[tuple[str, str], dict[str, str]] = {}
    for raw in review_rows:
        row = {key: _text(value) for key, value in raw.items()}
        key = (row.get("sku", ""), row.get("field_name", ""))
        if not all(key) or not row.get("revision_id") or not row.get("source_hash"):
            raise ValueError("REVIEW_ROW_IDENTITY_REQUIRED")
        if key in reviews:
            raise ValueError("DUPLICATE_REVIEW_FIELD")
        reviews[key] = row

    decisions_by_key: dict[tuple[str, str], dict[str, str]] = {}
    for raw in owner_decisions:
        row = {key: _text(value) for key, value in raw.items()}
        key = (row.get("sku", ""), row.get("field_name", ""))
        decision = row.get("owner_decision", "").upper()
        if decision not in _ACCEPTED_DECISIONS:
            raise ValueError("OWNER_DECISION_NOT_APPROVABLE")
        if key not in reviews or key in decisions_by_key:
            raise ValueError("OWNER_DECISION_REVIEW_IDENTITY_INVALID")
        review = reviews[key]
        if row.get("source_hash") != review["source_hash"] or row.get("parent_revision_id") != review["revision_id"]:
            raise ValueError("OWNER_DECISION_SOURCE_BINDING_MISMATCH")
        decisions_by_key[key] = row

    # Validate every requested mutation before approving even one field.  A
    # batch may contain a previously approved correction; that must fail the
    # whole import if its target conflicts, rather than leaving a partial
    # approval batch behind.
    plans: list[tuple[tuple[str, str], dict[str, str], dict[str, str], str, dict[str, Any]]] = []
    for key, decision in decisions_by_key.items():
        review = reviews[key]
        target = decision.get("target_text") or review.get("reviewed_value")
        if not target:
            raise ValueError("OWNER_DECISION_TARGET_REQUIRED")
        current = _current_revision(registry.path, *key)
        if not current:
            raise ValueError("OWNER_DECISION_CURRENT_REVISION_MISSING")
        if current["source_hash"] != review["source_hash"] or current["freshness_status"] != "FRESH":
            raise ValueError("OWNER_DECISION_STALE_SOURCE")
        if current["qa_status"] != "PASS" or (current["canonical_qa_status"] or "NOT_RUN") not in {"PASS", "NOT_REQUIRED"}:
            raise ValueError("OWNER_DECISION_QA_NOT_PASS")

        if current["review_status"] in {"APPROVED", "HUMAN_REVIEWED", "LOCKED"}:
            if current["target_text"] != target:
                raise ValueError("OWNER_DECISION_APPROVED_TARGET_CONFLICT")
        plans.append((key, decision, review, target, current))

    actions: list[dict[str, object]] = []
    for key, decision, review, target, current in plans:
        if current["review_status"] in {"APPROVED", "HUMAN_REVIEWED", "LOCKED"}:
            actions.append({"sku": key[0], "field_name": key[1], "status": "ALREADY_APPROVED", "revision_id": current["revision_id"], "target_text": target})
            continue

        parent_revision_id = current["revision_id"]
        if current["target_text"] == target:
            revision_id = parent_revision_id
            if not registry.approve_revision(revision_id, actor=actor):
                raise ValueError("OWNER_DECISION_APPROVAL_FAILED")
            status = "APPROVED_EXISTING"
        else:
            revision_id = registry.record_revision_for_sku(
                key[0], key[1], target, source_hash=review["source_hash"],
                provider=actor, model="owner-approval",
                repair_reason="WORKFLOW_V2_OWNER_REVIEW_APPROVAL",
                parent_revision_id=parent_revision_id, qa_status="PASS",
                canonical_qa_status="NOT_REQUIRED",
                provenance={
                    "owner_decision": decision["owner_decision"].upper(),
                    "owner_note": decision.get("owner_note", ""),
                    "reviewed_revision_id": review["revision_id"],
                    "review_artifact": decision.get("review_artifact", ""),
                },
            )
            if not registry.approve_revision(revision_id, actor=actor):
                raise ValueError("OWNER_DECISION_EDIT_APPROVAL_FAILED")
            status = "APPROVED_EDIT"
        actions.append({"sku": key[0], "field_name": key[1], "status": status, "revision_id": revision_id, "parent_revision_id": parent_revision_id, "target_text": target})
    return {"generated_at": _now(), "actor": actor, "approved_count": len(actions), "actions": actions}


def apply_owner_decisions_csv(
    registry: LocalizationRegistry, review_csv: Path, owner_decisions_csv: Path, *, actor: str, report_path: Path | None = None
) -> dict[str, object]:
    result = apply_owner_decisions(registry, _read_csv(review_csv), _read_csv(owner_decisions_csv), actor=actor)
    result.update({"review_csv": str(review_csv), "owner_decisions_csv": str(owner_decisions_csv)})
    if report_path is not None:
        Path(report_path).parent.mkdir(parents=True, exist_ok=True)
        Path(report_path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
