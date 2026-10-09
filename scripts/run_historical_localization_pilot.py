"""100-SKU source canary plus reviewed Chinese Registry/QA/delegated Apply.

This runner uses the existing production writer and translation runtime. It
never calls a provider and never fabricates a target or an approval identity.
"""
import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from action_tracker.config import load_settings
from action_tracker.database.connection import connect
from action_tracker.database.integration import regenerate_compatibility_exports
from action_tracker.database.production import ProductionWriter, apply_approved_localization_patches
from action_tracker.database.repository import ProductionRepository
from action_tracker.knowledge.storage import KnowledgeStore
from action_tracker.localization.canonical_qa import canonical_guard
from action_tracker.localization.contracts import SourceFacts, CANONICAL_TO_SOURCE, CANONICAL_TO_ZH, CANONICAL_AI_FIELDS
from action_tracker.localization.delegated_approval import ACTOR, VERSION
from action_tracker.localization.hashes import value_hash
from action_tracker.localization.history_audit import verified_backup
from action_tracker.localization.history_recovery import build_historical_source_bundle
from action_tracker.localization.product_family import context_for_field
from action_tracker.localization.qa import guard_translation
from action_tracker.localization.runtime_builder import build_translation_runtime
from action_tracker.services.hashing import localization_source_hash, localization_field_source_hash
from action_tracker.services.runtime import RunLock


def historical_records(repo, database):
    records = {r["sku"]: r for r in repo.load_current_export_records(include_non_current=True)}
    with connect(database) as db:
        for row in db.execute("SELECT official_sku,name,cat1,cat2,spec,description,details FROM product_localizations WHERE language='es'"):
            record = records.get(str(row[0]))
            if record and record["status"] != "CURRENT":
                # A missing historical source remains unavailable. Export's
                # business-name fallback is not this field's source evidence.
                facts = dict(zip(("name_es", "cat1_es", "cat2_es", "spec_es", "desc_es", "details_es"), row[1:]))
                record.update(facts)
                record["source_hash"] = localization_source_hash(facts)
    return records


def already_ready(record, database, field, target):
    provenance = record.get("zh_field_provenance", {}).get(field, {})
    with connect(database) as db:
        approved = db.execute("""SELECT COUNT(*) FROM translation_units u
            JOIN translation_revisions r ON r.revision_id=u.current_revision_id
            JOIN translation_source_versions s ON s.source_version_id=u.source_version_id
            WHERE s.official_sku=? AND s.source_hash=? AND u.field_name=?
            AND u.freshness_status='FRESH' AND r.review_status='APPROVED'
            AND r.qa_status='PASS' AND r.canonical_qa_status IN ('PASS','NOT_REQUIRED')
            AND r.target_hash=? AND r.target_text=? AND r.source_hash=s.source_hash
            AND NOT EXISTS (SELECT 1 FROM translation_qa_findings q WHERE q.revision_id=r.revision_id
                AND q.status='OPEN' AND q.severity IN ('BLOCKER','ERROR','HIGH'))""", (record["sku"], record["source_hash"],
                CANONICAL_TO_SOURCE[field], value_hash(target), target)).fetchone()[0]
    return bool(approved and provenance.get("value") == target
        and provenance.get("review_status") == "APPROVED"
        and provenance.get("freshness_status") in {"CURRENT", "FRESH"}
        and provenance.get("approved_by")
        and provenance.get("source_hash") == localization_field_source_hash(record, field))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--review-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--projection-root", type=Path, required=True)
    parser.add_argument("--authorization-ref", required=True)
    parser.add_argument("--authorization-text", required=True)
    parser.add_argument("--source-manifest", type=Path)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--batch-id", default="historical_reviewed_pilot_20261009")
    args = parser.parse_args()
    cfg = load_settings(); cfg["storage"] = {**cfg.get("storage", {}), "mode": "SQLITE_PRIMARY", "db_path": args.database}
    for key in ("state", "temp", "backups", "exports"):
        cfg["paths"][key] = args.projection_root / key
    cfg["paths"]["master"] = args.projection_root / "master" / "Action_Master.xlsx"
    if not cfg["paths"]["master"].exists():
        raise ValueError("PILOT_MASTER_COPY_REQUIRED")
    args.output.mkdir(parents=True, exist_ok=True)
    lock = RunLock(cfg["paths"]["state"]); lock.acquire("historical-reviewed-pilot", command="historical-localization")
    try:
        backup = verified_backup(args.database, args.backup) if args.backup else None
        source_result = None
        if args.source_manifest:
            source_rows = [json.loads(line) for line in args.source_manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
            bundle, source_result = build_historical_source_bundle(args.database, source_rows,
                run_id=("historical_source_pilot_100_20261009" if args.batch_id == "historical_reviewed_pilot_20261009" else args.batch_id + "_source"), base_commit_id=ProductionRepository(args.database).current_head(), run_date="2026-10-09")
            if source_result["recovered_fields"]:
                source_result["commit_id"] = ProductionWriter(args.database, role="PRIMARY").commit(bundle)
            (args.output / "source_recovery.json").write_text(json.dumps(source_result, ensure_ascii=False, indent=2), encoding="utf-8")
        runtime = build_translation_runtime(cfg, db_path=args.database, allow_provider=False)
        repo = ProductionRepository(args.database)
        records = historical_records(repo, args.database)
        reviews = json.loads(args.review_file.read_text(encoding="utf-8"))
        artifact_hash = hashlib.sha256(args.review_file.read_bytes()).hexdigest()
        outcomes = []; grant_reviews = {}; ready = []
        for review in reviews:
            if review["decision"] != "CORRECTED":
                continue
            sku = review["sku"]; field = review["field"]; target = review["after"]
            record = records[sku]
            evidence = review.get("source_evidence", {})
            selected = evidence.get("selected") or {}
            if evidence.get("status") != "EVIDENCE_AVAILABLE" or selected.get("text") != review["source"]:
                raise ValueError("PILOT_REVIEW_SOURCE_EVIDENCE_REQUIRED")
            artifact = str(selected.get("reference", "")).split("::")[0]
            if not artifact.startswith("PRIMARY:") and hashlib.sha256(Path(artifact).read_bytes()).hexdigest() != selected.get("file_hash"):
                raise ValueError("PILOT_REVIEW_SOURCE_ARTIFACT_CHANGED")
            if record["status"] == "CURRENT": raise ValueError("PILOT_HISTORICAL_SCOPE_REQUIRED")
            if field not in CANONICAL_AI_FIELDS: raise ValueError("PILOT_REVIEW_FIELD_NOT_SUPPORTED")
            if str(record.get(CANONICAL_TO_SOURCE[field]) or "") != review["source"]:
                outcomes.append({"sku": sku, "field": field, "status": "SOURCE_VERSION_REVIEW_REQUIRED"}); continue
            if record.get(CANONICAL_TO_ZH[field]) == target:
                outcomes.append({"sku": sku, "field": field,
                    "status": "NO_OP" if already_ready(record, args.database, field, target) else "METADATA_REVIEW_REQUIRED"}); continue
            plan = runtime.resolver.engine.resolve(record); context = plan.context
            resolution = runtime.resolver.resolve_field(record, field, allow_provider=False, context=context)
            terms = tuple(resolution.provenance.get("terminology") or ())
            qa = guard_translation(SourceFacts.from_record(record), {field: target}, (field,), terminology=terms,
                                   semantic_facts=plan.semantic_facts)
            canonical = canonical_guard(context_for_field(context, field), {field: target}, production=False) if context else {"status": "NOT_REQUIRED"}
            if qa["status"] != "PASS" or canonical["status"] not in {"PASS", "NOT_REQUIRED"}:
                outcomes.append({"sku": sku, "field": field, "status": "QA_REVIEW_REQUIRED", "qa": qa, "canonical": canonical}); continue
            facts = {key: record.get(key) for key in ("name_es", "cat1_es", "cat2_es", "spec_es", "desc_es", "details_es")}
            source_hash = localization_source_hash(facts)
            # None is unavailable evidence; register_source skips those units.
            # A reconstructed aggregate has no single official observation
            # timestamp. Original field dates remain in immutable evidence.
            runtime.registry.register_source(sku, facts, source_hash, observed_at="",
                source_run_id=args.batch_id, source_quality_status="VALID")
            with connect(args.database) as db:
                existing = db.execute("SELECT r.revision_id FROM translation_revisions r JOIN translation_units u ON u.unit_id=r.unit_id JOIN translation_source_versions s ON s.source_version_id=u.source_version_id WHERE s.official_sku=? AND s.source_hash=? AND u.field_name=? AND r.target_hash=? AND r.repair_reason='HISTORICAL_SOURCE_BOUND_SEMANTIC_REVIEW' ORDER BY r.created_at DESC LIMIT 1", (sku, source_hash, CANONICAL_TO_SOURCE[field], value_hash(target))).fetchone()
            revision = str(existing[0]) if existing else runtime.registry.record_revision_for_sku(sku, field, target,
                source_hash=source_hash, provider="codex_semantic_review", model="CODEX", repair_reason="HISTORICAL_SOURCE_BOUND_SEMANTIC_REVIEW",
                qa_status=qa["status"], canonical_qa_status=canonical["status"],
                provenance={"review": review, "review_artifact_sha256": artifact_hash, "qa": qa, "canonical": canonical})
            grant_reviews[revision] = {"sku": sku, "field": field, "source_hash": source_hash, "target_hash": value_hash(target),
                "semantic_status": "PASS", "source_evidence_status": "VERIFIED", "evidence_ref": str(args.review_file.resolve()), "evidence_sha256": artifact_hash}
            ready.append(revision); outcomes.append({"sku": sku, "field": field, "revision": revision, "status": "QA_PASS"})
            (args.output / "review_progress.json").write_text(json.dumps(outcomes, ensure_ascii=False, indent=2), encoding="utf-8")
        grant_path = args.output / "owner_delegation.json"
        grant = {"contract": VERSION, "actor": ACTOR, "authorization_ref": args.authorization_ref,
                 "authorization_text": args.authorization_text, "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), "reviews": grant_reviews}
        if grant_path.exists():
            saved = json.loads(grant_path.read_text(encoding="utf-8"))
            if saved["reviews"] == grant_reviews: grant = saved
            elif ready: raise ValueError("PILOT_DELEGATION_CHANGED_REQUIRES_NEW_BATCH")
        elif ready: grant_path.write_text(json.dumps(grant, ensure_ascii=False, indent=2), encoding="utf-8")
        result = {"status": "NO_OP", "applied_fields": 0}
        if ready:
            for revision in ready: runtime.registry.approve_revision_delegated(revision, actor=ACTOR, grant=grant)
            head = repo.current_head()
            staged = KnowledgeStore(args.database, role="PRIMARY").stage_approved_registry_patches(
                expected_base_commit_id=head, actor=ACTOR, revision_ids=ready, delegated_approval=grant)
            if staged["stale_source_rows"]: raise ValueError("PILOT_STALE_SOURCE")
            if staged["patch_ids"]:
                result = apply_approved_localization_patches(args.database, patch_ids=staged["patch_ids"],
                    expected_base_commit_id=head, actor="service:historical-localization-apply", run_id=args.batch_id, delegated_approval=grant)
        head = repo.current_head()
        with connect(args.database) as db:
            previous_sync = db.execute("SELECT status,master_sha256,known_sha256,offline_sha256 FROM export_sync WHERE commit_id=?", (head,)).fetchone()
        files = (cfg["paths"]["master"], cfg["paths"]["state"] / "known_skus.csv", cfg["paths"]["state"] / "offline_skus.csv")
        synced = (previous_sync and previous_sync[0] == "SUCCESS"
            and all(path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == expected
                for path, expected in zip(files, previous_sync[1:])))
        sync = {"status": "ALREADY_SYNCED", "commit_id": head} if synced else regenerate_compatibility_exports(cfg, head)
        report = {"outcomes": outcomes, "apply": result, "sync": sync, "qwen_calls": 0,
                  "backup": backup, "source_recovery": source_result}
        (args.output / "pilot_review_apply.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"applied_fields": result["applied_fields"], "reviewed_fields": len(outcomes), "qwen_calls": 0}, ensure_ascii=False))
    finally:
        lock.release()


if __name__ == "__main__": main()
