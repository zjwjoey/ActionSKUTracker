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
from ..localization.registry.repository import LocalizationRegistry
from ..localization.runtime_builder import build_translation_runtime


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
    runtime = build_translation_runtime({"project_root": output_dir, "storage": {"db_path": str(db_path)}, "paths": {}, "localization": {"ai": {"enabled": False}}}, db_path=db_path)
    runtime.resolver.provider = provider
    runtime.worker.resolver.provider = provider
    registry_result = registry.ingest_records([dict(record)], source_run_id=source_run_id, observed_at=now)
    worker_result = runtime.worker.process_once(limit=50, worker_id="workflow-v2-canary")
    with connect(db_path) as db:
        revisions = [str(row[0]) for row in db.execute("""SELECT r.revision_id FROM translation_revisions r
            JOIN translation_units u ON u.current_revision_id=r.revision_id
            JOIN translation_source_versions s ON s.source_version_id=u.source_version_id
            WHERE s.source_run_id=? AND r.qa_status='PASS'""", (source_run_id,)).fetchall()]
    approved = [revision_id for revision_id in revisions if registry.approve_revision(revision_id, actor="human:workflow-v2-canary")]
    store = KnowledgeStore(db_path, role="PRIMARY")
    preview = store.preview_approved_registry_apply()
    staged = store.stage_approved_registry_patches(expected_base_commit_id=commit_id, actor="human:workflow-v2-canary")
    applied = apply_approved_localization_patches(db_path, patch_ids=staged["patch_ids"], expected_base_commit_id=commit_id,
                                                  actor="service:workflow-v2-canary", run_id=f"{source_run_id}-apply") if staged["patch_ids"] else {"status": "NOOP", "applied_fields": 0}
    with connect(db_path) as db:
        zh = db.execute("SELECT name,cat1,cat2,spec,description,details FROM product_localizations WHERE official_sku=? AND language='zh'", (sku,)).fetchone()
    result = {"status": "PASS" if worker_result.failed == 0 and worker_result.blocked == 0 and len(approved) == len(revisions) and applied.get("applied_fields", 0) == len(approved) and zh else "FAIL",
              "database": str(db_path), "source_commit_id": commit_id, "source_hash": facts.source_hash,
              "provider": getattr(provider, "provider", "unknown"), "model": getattr(provider, "model", "unknown"),
              "registry": registry_result, "worker": worker_result.as_dict(), "revision_ids": revisions, "approved_revision_count": len(approved),
              "preview_count": len(preview), "staged_fields": staged["staged_fields"], "applied": applied,
              "zh_present": bool(zh)}
    (output_dir / "canary_report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return result
