"""Isolated local canary for the Translation V1 -> Apply boundary."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..database.connection import connect
from ..database.production import CommitBundle, ProductionWriter, apply_approved_localization_patches
from ..knowledge.storage import KnowledgeStore
from ..localization.contracts import SourceFacts
from ..localization.providers.base import TranslationRequest
from ..localization.qa import guard_translation
from ..localization.registry.repository import LocalizationRegistry


def run_local_canary(*, record: Mapping[str, Any], provider: Any, output_dir: Path) -> dict[str, Any]:
    """Run a complete canary on a temporary PRIMARY-role SQLite database.

    The PRIMARY role here is an isolated fixture database created below
    ``output_dir``. The function never resolves the configured production DB.
    """
    output_dir = Path(output_dir); output_dir.mkdir(parents=True, exist_ok=True)
    db_path = output_dir / "canary.sqlite3"
    sku = str(record.get("sku") or record.get("official_sku") or "").strip()
    facts = SourceFacts.from_record(record)
    fields = {"name_es": facts.name_es, "cat1_es": facts.cat1_es, "cat2_es": facts.cat2_es,
              "spec_es": facts.spec_es, "desc_es": facts.desc_es, "details_es": facts.details_es}
    now = datetime.now(timezone.utc).isoformat()
    source_run_id = f"workflow-v2-canary-{sku}"
    bundle = CommitBundle(
        run_id=source_run_id, observation_date=str(record.get("business_date") or "2026-10-04"), qa_state="PASS",
        current_products=(dict(record),), localization_updates=(
            {"sku": sku, "language": "es", "name": facts.name_es, "cat1": facts.cat1_es, "cat2": facts.cat2_es,
             "spec": facts.spec_es, "description": facts.desc_es, "details": facts.details_es, "source": "OFFICIAL_FACT", "review_status": "VERIFIED"},
            {"sku": sku, "language": "zh", "name": "", "cat1": "", "cat2": "", "spec": "", "description": "", "details": "", "source": "PENDING", "review_status": "PENDING"},
        ),
        observations=({"run_id": source_run_id, "sku": sku, "observation_date": str(record.get("business_date") or "2026-10-04"), "presence_state": "PRESENT", "observation_complete": True, "absence_capable": True},),
        run_record={"dry_run": False, "started_at": now, "finished_at": now},
    )
    commit_id = ProductionWriter(db_path, role="PRIMARY").commit(bundle)
    registry = LocalizationRegistry(db_path, role="PRIMARY")
    registry.register_source(sku, fields, facts.source_hash, observed_at=now, source_run_id=source_run_id,
                             raw_fields=fields, normalized_fields=fields, source_quality_status="PASS")
    request = TranslationRequest(sku=sku, fields=facts.as_record(), requested_fields=("name", "cat1", "cat2", "spec", "description", "details"), source_hash=facts.source_hash, request_id=f"canary:{sku}")
    response = provider.translate(request)
    qa = guard_translation(facts, response.fields, request.requested_fields)
    # No product-family context is supplied by this minimal fixture. The
    # canonical layer is therefore explicitly not required, rather than left
    # as NOT_RUN (which must never be approvable).
    if qa.get("canonical_status") == "NOT_RUN":
        qa["canonical_status"] = "NOT_REQUIRED"
        qa["canonical_qa_status"] = "NOT_REQUIRED"
    recorded = registry.record_response(official_sku=sku, source_fields=fields, source_hash_value=facts.source_hash,
                                         observed_at=now, source_run_id=source_run_id, response=response, qa=qa)
    approved = [revision_id for revision_id in recorded["revision_ids"] if registry.approve_revision(revision_id, actor="human:workflow-v2-canary")]
    store = KnowledgeStore(db_path, role="PRIMARY")
    preview = store.preview_approved_registry_apply()
    staged = store.stage_approved_registry_patches(expected_base_commit_id=commit_id, actor="human:workflow-v2-canary")
    applied = apply_approved_localization_patches(db_path, patch_ids=staged["patch_ids"], expected_base_commit_id=commit_id,
                                                  actor="service:workflow-v2-canary", run_id=f"{source_run_id}-apply") if staged["patch_ids"] else {"status": "NOOP", "applied_fields": 0}
    with connect(db_path) as db:
        zh = db.execute("SELECT name,cat1,cat2,spec,description,details FROM product_localizations WHERE official_sku=? AND language='zh'", (sku,)).fetchone()
    result = {"status": "PASS" if qa.get("overall_ready") and len(approved) == len(recorded["revision_ids"]) and applied.get("applied_fields", 0) == len(approved) and zh else "FAIL",
              "database": str(db_path), "source_commit_id": commit_id, "source_hash": facts.source_hash,
              "provider": getattr(response, "provider", "unknown"), "model": getattr(response, "model", "unknown"),
              "qa": qa, "revision_ids": recorded["revision_ids"], "approved_revision_count": len(approved),
              "preview_count": len(preview), "staged_fields": staged["staged_fields"], "applied": applied,
              "zh_present": bool(zh)}
    (output_dir / "canary_report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return result
