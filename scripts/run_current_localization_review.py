"""Apply an independently reviewed finite CURRENT localization manifest.

Uses the existing Registry, delegated approval and immutable Apply APIs.
No collection, provider calls, direct PRIMARY upserts or historical work.
"""
import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from action_tracker.config import load_settings
from action_tracker.database.connection import connect
from action_tracker.database.integration import regenerate_compatibility_exports
from action_tracker.database.production import apply_approved_localization_patches
from action_tracker.database.repository import ProductionRepository
from action_tracker.knowledge.storage import KnowledgeStore
from action_tracker.localization.contracts import SourceFacts, SemanticFact
from action_tracker.localization.delegated_approval import CURRENT_ACTOR, CURRENT_VERSION
from action_tracker.localization.hashes import value_hash
from action_tracker.localization.history_audit import verified_backup
from action_tracker.localization.product_family import context_for_field
from action_tracker.localization.qa import guard_translation
from action_tracker.localization.runtime_builder import build_translation_runtime
from action_tracker.services.hashing import localization_source_hash, localization_field_source_hash
from action_tracker.services.runtime import RunLock


FIELDS = dict(zip(("name", "cat1", "cat2", "spec", "description", "details"),
                  ("name_es", "cat1_es", "cat2_es", "spec_es", "desc_es", "details_es")))
ZH = dict(zip(FIELDS, ("name_zh", "cat1_zh", "cat2_zh", "spec_zh", "desc_zh", "details_zh")))


def field_already_ready(record, field, target):
    provenance = (record.get("zh_field_provenance") or {}).get(field) or {}
    return (record.get(ZH[field]) == target and provenance.get("value") == target
            and provenance.get("review_status") in {"APPROVED", "VERIFIED", "HUMAN_REVIEWED"}
            and provenance.get("freshness_status") == "CURRENT"
            and bool(provenance.get("approved_by"))
            and provenance.get("source_hash") == localization_field_source_hash(record, field))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--scope", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--verified-backup-report", type=Path,
                        help="Reuse a verified task-start backup only while committed HEAD is unchanged")
    args = parser.parse_args()
    cfg = load_settings()
    runtime = build_translation_runtime(cfg, allow_provider=False)
    repo = ProductionRepository(runtime.db_path)
    scope = json.loads(args.scope.read_text(encoding="utf-8"))
    reviews = json.loads(args.review.read_text(encoding="utf-8"))
    if scope.get("scope") != "TODAY_ON_SALE_ONLY" or not scope.get("authorization_text"):
        raise ValueError("CURRENT_REVIEW_SCOPE_OR_AUTHORIZATION_REQUIRED")
    identities = [(r["sku"], r["field"]) for r in reviews]
    if len(set(identities)) != len(identities):
        raise ValueError("CURRENT_REVIEW_DUPLICATE_FIELDS")
    if args.commit and not (cfg["localization"]["production_apply_enabled"] and cfg["knowledge"]["production_apply_enabled"]):
        raise PermissionError("CURRENT_PRODUCTION_APPLY_DISABLED")
    args.output.mkdir(parents=True, exist_ok=True)
    lock = RunLock(Path(cfg["paths"]["state"]))
    lock.acquire(args.batch_id, command="current-localization-reviewed-apply")
    try:
        records = {r["sku"]: r for r in repo.load_current_export_records()}
        evidence_hash = hashlib.sha256(args.review.read_bytes()).hexdigest()
        outcomes, prepared = [], []
        for review in reviews:
            sku, field = review["sku"], review["field"]
            record = records.get(sku)
            if sku not in scope["current_skus"] or not record or field not in FIELDS:
                raise ValueError("CURRENT_REVIEW_SCOPE_CHANGED")
            if review.get("review_model") != "CODEX" or review.get("decision") == "REVIEW_REQUIRED":
                outcomes.append({"sku": sku, "field": field, "status": "OWNER_REVIEW_REQUIRED", "reason": review.get("note")})
                continue
            if not str(review.get("note") or "").strip():
                raise ValueError("SEMANTIC_REVIEW_EVIDENCE_REQUIRED")
            source_hash = localization_source_hash(record)
            if source_hash != review["source_hash"] or any(record.get(k) != review["context"].get(k) for k in FIELDS.values()):
                raise ValueError("CURRENT_REVIEW_SOURCE_CHANGED")
            if record.get(ZH[field]) != review["current_zh"] and record.get(ZH[field]) != review["target"]:
                raise ValueError("CURRENT_REVIEW_TARGET_CHANGED")
            if field_already_ready(record, field, review["target"]):
                outcomes.append({"sku": sku, "field": field, "status": "ALREADY_READY"})
                continue
            if field in {"cat1", "cat2"}:
                from scripts.run_historical_localization_pilot import reviewed_category_mapping_status
                category = reviewed_category_mapping_status({"field": field, "after": review["target"]},
                    record, runtime.resolver.engine.knowledge)
                if category != "READY":
                    outcomes.append({"sku": sku, "field": field, "status": category})
                    continue
            plan = runtime.resolver.engine.resolve(record)
            context = context_for_field(plan.context, field) if plan.context else None
            resolution = runtime.resolver.resolve_field(record, field, allow_provider=False, context=plan.context)
            facts = tuple(plan.semantic_facts) + tuple(SemanticFact(**f) for f in review.get("semantic_facts_additions", []))
            qa = guard_translation(SourceFacts.from_record(record), {field: review["target"]}, (field,),
                semantic_facts=facts, terminology=tuple(resolution.provenance.get("terminology") or ()), context=context)
            if not qa["overall_ready"]:
                outcomes.append({"sku": sku, "field": field, "status": "QA_BLOCKED", "qa": qa})
                continue
            prepared.append((review, qa))
        (args.output / "preflight.json").write_text(json.dumps(outcomes, ensure_ascii=False, indent=2), encoding="utf-8")
        if not args.commit:
            print(json.dumps({"ready": len(prepared), "blocked": sum(r["status"] != "ALREADY_READY" for r in outcomes), "already_ready": sum(r["status"] == "ALREADY_READY" for r in outcomes), "production_writes": 0}))
            return
        if not prepared:
            report = {"reviewed_fields": 0, "blocked": [r for r in outcomes if r["status"] != "ALREADY_READY"],
                      "already_ready": sum(r["status"] == "ALREADY_READY" for r in outcomes),
                      "apply": {"status": "NO_OP", "applied_fields": 0}, "qwen_calls": 0}
            (args.output / "resume_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False))
            return
        backup_path = Path(cfg["paths"]["backups"]) / "current_localization" / (args.batch_id + ".sqlite3")
        backup_report = args.output / "backup.json"
        if not backup_report.exists():
            if args.verified_backup_report:
                backup = json.loads(args.verified_backup_report.read_text(encoding="utf-8"))
                restored = Path(backup["restore"])
                if (not backup.get("logical_parity") or backup.get("integrity") != "ok"
                        or backup.get("foreign_key_violations")
                        or hashlib.sha256(Path(backup["backup"]).read_bytes()).hexdigest() != backup["sha256"]
                        or ProductionRepository(restored).current_head() != repo.current_head()):
                    raise ValueError("RECOVERY_BACKUP_NOT_VALID_FOR_CURRENT_HEAD")
                backup = {**backup, "reused_for_recovery": True}
            else:
                backup = verified_backup(runtime.db_path, backup_path)
            backup_report.write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")
        grant_reviews, ready = {}, []
        for review, qa in prepared:
            sku, field, target = review["sku"], review["field"], review["target"]
            with connect(runtime.db_path) as db:
                old = db.execute("SELECT r.revision_id,r.target_text,r.qa_status,r.canonical_qa_status,r.review_status,r.approved_by FROM translation_units u JOIN translation_source_versions s ON s.source_version_id=u.source_version_id LEFT JOIN translation_revisions r ON r.revision_id=u.current_revision_id WHERE s.official_sku=? AND s.source_hash=? AND u.field_name IN (?,?)",
                    (sku, review["source_hash"], field, FIELDS[field])).fetchone()
            reusable = (old and old[1] == target and old[2] == "PASS" and old[3] in {"PASS", "NOT_REQUIRED"}
                        and old[4] in {"PENDING", "REVIEW_REQUIRED"})
            revision = str(old[0]) if reusable else runtime.registry.record_revision_for_sku(sku, field, target,
                source_hash=review["source_hash"], provider="codex_semantic_review", model="CODEX",
                parent_revision_id=str(old[0]) if old and old[0] else None,
                repair_reason="CURRENT_SOURCE_BOUND_SEMANTIC_REVIEW", qa_status="PASS",
                canonical_qa_status=qa["canonical_qa_status"], provenance={"review": review, "review_artifact_sha256": evidence_hash, "qa": qa})
            grant_reviews[revision] = {"sku": sku, "field": field, "source_hash": review["source_hash"],
                "target_hash": value_hash(target), "semantic_status": "PASS", "source_evidence_status": "VERIFIED",
                "evidence_ref": str(args.review.resolve()), "evidence_sha256": evidence_hash}
            ready.append(revision)
        grant_path = args.output / "owner_delegation.json"
        grant = {"contract": CURRENT_VERSION, "actor": CURRENT_ACTOR,
            "authorization_ref": scope["authorization_ref"], "authorization_text": scope["authorization_text"],
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "collection_run_id": scope["collection_run_id"], "business_date": scope["business_date"],
            "current_skus": scope["current_skus"], "reviews": grant_reviews}
        if grant_path.exists():
            saved = json.loads(grant_path.read_text(encoding="utf-8"))
            if saved["reviews"] != grant_reviews:
                raise ValueError("CURRENT_GRANT_CHANGED_REQUIRES_NEW_BATCH")
            grant = saved
        else:
            grant_path.write_text(json.dumps(grant, ensure_ascii=False, indent=2), encoding="utf-8")
        for revision in ready:
            runtime.registry.restore_unit_freshness_delegated(revision, actor=CURRENT_ACTOR, grant=grant)
            runtime.registry.approve_revision_delegated(revision, actor=CURRENT_ACTOR, grant=grant)
        head = repo.current_head()
        staged = KnowledgeStore(runtime.db_path, role="PRIMARY").stage_approved_registry_patches(
            expected_base_commit_id=head, actor=CURRENT_ACTOR, revision_ids=ready, include_noop_rebinds=True, delegated_approval=grant)
        if staged["stale_source_rows"]:
            raise ValueError("CURRENT_STAGED_SOURCE_CHANGED")
        applied = apply_approved_localization_patches(runtime.db_path, patch_ids=staged["patch_ids"],
            expected_base_commit_id=head, actor="service:current-localization-apply", run_id=args.batch_id, delegated_approval=grant)
        sync = regenerate_compatibility_exports(cfg, repo.current_head())
        report = {"reviewed_fields": len(prepared), "blocked": outcomes, "apply": applied, "sync": sync, "qwen_calls": 0}
        (args.output / "apply_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(json.dumps({"reviewed": len(prepared), "blocked": len(outcomes), "apply": applied}, ensure_ascii=False, default=str))
    finally:
        lock.release()


if __name__ == "__main__":
    main()
