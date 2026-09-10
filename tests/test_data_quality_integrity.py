from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from action_tracker.database.connection import connect
from action_tracker.database.integration import commit_daily_bundle
from action_tracker.database.production import CommitBundle, ProductionDatabaseError
from action_tracker.database.schema import migrate_v2
from action_tracker.data_quality.collection import build_collection_metrics, evaluate_collection, evaluate_and_persist, validate_collection_override
from action_tracker.data_quality.collection.gates import collection_commit_allowed
from action_tracker.data_quality.contracts import DataQualityIssue, issue_id
from action_tracker.data_quality.historical import audit_history, approve_candidate, apply_repair_batch, build_repair_candidates, prepare_formal_correction, verify_repair_batch, write_repair_preview
from action_tracker.data_quality.master_gate import audit_master_quality
from action_tracker.data_quality.repository import DataQualityRepository
from action_tracker.data_quality.schema import ensure_data_quality_schema
from action_tracker.localization.release_gate import audit_research_release


def _db(tmp_path: Path, *, clean: bool = True) -> Path:
    path = tmp_path / "action.db"
    migrate_v2(path, role="SHADOW")
    ensure_data_quality_schema(path)
    with connect(path) as db:
        db.execute("""INSERT INTO products(canonical_id,official_sku,name_es,name_zh,current_price,original_price,
            raw_badges,status,product_url,image_url,created_at,updated_at)
            VALUES('ACT1001','1001','Producto','商品',1.0,2.0,'','CURRENT','https://example/1001','https://example/image',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""")
        db.execute("UPDATE products SET source_hash='product-source-hash' WHERE official_sku='1001'")
        db.execute("""INSERT INTO product_localizations(official_sku,language,name,cat1,cat2,spec,unit_price,description,details,source,review_status,updated_at,source_hash,freshness_status)
            VALUES('1001','es','Producto','Hogar','Limpieza','1 unidad','1','Texto','Material: Plástico','official','APPROVED',CURRENT_TIMESTAMP,'es-hash','CURRENT')""")
        db.execute("""INSERT INTO product_localizations(official_sku,language,name,cat1,cat2,spec,unit_price,description,details,source,review_status,updated_at,source_hash,freshness_status)
            VALUES('1001','zh','商品','家居','清洁','1件','1','中文描述','材质：塑料','dictionary','APPROVED',CURRENT_TIMESTAMP,'zh-hash','CURRENT')""")
        for field in ("name", "cat1", "cat2", "spec", "description", "details"):
            db.execute("""INSERT INTO localization_fields(official_sku,language,field_name,value,source,review_status,source_hash,updated_at,applied_commit_id)
                VALUES('1001','zh',?,?, 'dictionary','APPROVED','source-hash',CURRENT_TIMESTAMP,'commit-1')""", (field, "value"))
        if not clean:
            db.execute("UPDATE products SET original_price=current_price,raw_badges='7.90 €/kg' WHERE official_sku='1001'")
            db.execute("DELETE FROM localization_fields WHERE official_sku='1001' AND field_name='details'")
    return path


def test_issue_id_is_stable_and_contract_serializes():
    one = issue_id(issue_type="HTML_CONTAMINATION", scope="HISTORICAL", official_sku="1001", field_name="details", source_hash="h")
    two = issue_id(issue_type="HTML_CONTAMINATION", scope="HISTORICAL", official_sku="1001", field_name="details", source_hash="h")
    assert one == two
    assert DataQualityIssue(issue_type="HTML_CONTAMINATION", severity="HIGH", scope="HISTORICAL", official_sku="1001").issue_id


def test_historical_audit_detects_fixture_issues_and_is_idempotent(tmp_path: Path):
    path = _db(tmp_path)
    with connect(path) as db:
        db.execute("UPDATE products SET original_price=current_price,raw_badges='7.90 €/kg' WHERE official_sku='1001'")
        db.execute("UPDATE product_localizations SET details='<p>Leer más</p>',cat2='' WHERE official_sku='1001' AND language='es'")
        db.execute("INSERT INTO price_history(canonical_id,official_sku,observed_at,new_price,change_type) VALUES('X','ORPHAN','2026-01-01',1,'DOWN')")
        db.execute("INSERT INTO event_history(canonical_id,official_sku,occurred_at,event_type) VALUES('X','ORPHAN','2026-01-01','FIRST_SEEN')")
    first = audit_history(path, persist=True)
    second = audit_history(path, persist=True)
    assert {item.issue_type for item in first.issues} >= {"INVALID_ORIGINAL_PRICE", "PROMOTION_FIELD_CONTAMINATION", "HTML_CONTAMINATION", "ORPHAN_PRICE_HISTORY", "ORPHAN_EVENT_HISTORY", "CATEGORY_MISSING"}
    assert {item.issue_id for item in first.issues} == {item.issue_id for item in second.issues}
    assert len(DataQualityRepository(path).list_issues()) == len(first.issues)


def test_historical_audit_keeps_unresolved_synthetic_identity_unresolved(tmp_path: Path):
    path = _db(tmp_path)
    with connect(path) as db:
        db.execute("INSERT INTO runs(run_id,run_date,status,qa_state,dry_run) VALUES('archive-run','2026-01-01','SUCCESS','PASS',1)")
        db.execute(
            "INSERT INTO source_records(source_record_id,run_id,source_name,official_sku,payload_json) VALUES(?,?,?,?,?)",
            ("archive-1", "archive-run", "legacy", None, json.dumps({"canonical_id": "ACT-SYNTH-1", "name": "legacy"})),
        )
    result = audit_history(path)
    assert any(issue.issue_type == "UNRESOLVED_HISTORICAL_IDENTITY" for issue in result.issues)


def test_wide_localization_html_issue_preserves_source_hash(tmp_path: Path):
    path = _db(tmp_path)
    with connect(path) as db:
        db.execute("UPDATE product_localizations SET details='<p>Texto</p>' WHERE official_sku='1001' AND language='es'")
    result = audit_history(path)
    issue = next(item for item in result.issues if item.issue_type == "HTML_CONTAMINATION" and item.field_name == "details")
    assert issue.source_hash == "es-hash"


def test_promotion_classifier_accepts_real_promotion_semantics_and_raw_badges(tmp_path: Path):
    path = _db(tmp_path)
    with connect(path) as db:
        db.execute("ALTER TABLE products ADD COLUMN promotion_label TEXT")
        for value in ("20% de descuento", "Oferta semanal", "Descuento 30%", "Discount 30%", "rebaja especial", "promotion activa"):
            db.execute("UPDATE products SET promotion_label=?,raw_badges='Nuevo' WHERE official_sku='1001'", (value,))
            assert not any(item.issue_type == "PROMOTION_FIELD_CONTAMINATION" for item in audit_history(path).issues)
            assert audit_master_quality(path).counts.get("PROMOTION_FIELD_CONTAMINATION", 0) == 0


def test_promotion_contamination_is_context_aware_and_covers_spanish_unit_prices(tmp_path: Path):
    path = _db(tmp_path)
    with connect(path) as db:
        db.execute("ALTER TABLE products ADD COLUMN promotion_label TEXT")
        db.execute("UPDATE products SET raw_badges='Nuevo',promotion_label='0,59 €/unidad' WHERE official_sku='1001'")
    historical = audit_history(path)
    assert sum(issue.issue_type == "PROMOTION_FIELD_CONTAMINATION" for issue in historical.issues) == 1
    assert historical.issues[0].field_name == "promotion_label"
    master = audit_master_quality(path)
    assert master.counts["PROMOTION_FIELD_CONTAMINATION"] == 1


def test_repair_candidates_are_idempotent_and_require_approval(tmp_path: Path):
    path = _db(tmp_path)
    with connect(path) as db:
        db.execute("UPDATE products SET original_price=current_price WHERE official_sku='1001'")
    result = build_repair_candidates(path)
    again = build_repair_candidates(path, batch_id=result["repair_batch_id"])
    assert again["repair_batch_id"] == result["repair_batch_id"]
    candidates = DataQualityRepository(path).candidates(result["repair_batch_id"])
    assert candidates
    with pytest.raises(Exception, match="REPAIR_APPROVAL_REQUIRED"):
        apply_repair_batch(path, result["repair_batch_id"], commit=True, actor="human:tester")
    assert all(candidate["candidate_status"] == "REVIEW_REQUIRED" for candidate in candidates)


def test_repair_without_source_evidence_is_blocked(tmp_path: Path):
    path = _db(tmp_path)
    with connect(path) as db:
        db.execute("UPDATE products SET original_price=current_price WHERE official_sku='1001'")
    result = build_repair_candidates(path)
    candidate = DataQualityRepository(path).candidates(result["repair_batch_id"])[0]
    with connect(path) as db:
        db.execute("UPDATE repair_candidates SET source_hash=NULL WHERE candidate_id=?", (candidate["candidate_id"],))
    approve_candidate(path, candidate["candidate_id"], reviewer="human:alice")
    with pytest.raises(Exception, match="SOURCE_HASH_REQUIRED"):
        apply_repair_batch(path, result["repair_batch_id"], commit=True, actor="human:alice")


def test_approved_repair_applies_only_to_fixture_and_verifies(tmp_path: Path):
    path = _db(tmp_path)
    with connect(path) as db:
        db.execute("UPDATE products SET original_price=current_price WHERE official_sku='1001'")
    result = build_repair_candidates(path)
    candidates = DataQualityRepository(path).candidates(result["repair_batch_id"])
    for candidate in candidates:
        approve_candidate(path, candidate["candidate_id"], reviewer="human:alice")
    applied = apply_repair_batch(path, result["repair_batch_id"], commit=True, actor="human:alice")
    assert applied["status"] == "FIXTURE_APPLIED"
    assert applied["result_commit_id"] is None
    with connect(path) as db:
        row = db.execute("SELECT issue_id,candidate_status,reviewed_by,applied_by FROM repair_candidates WHERE repair_batch_id=?", (result["repair_batch_id"],)).fetchone()
        assert row[2] == "human:alice"
        assert row[3] == "human:alice"
        assert db.execute("SELECT status FROM data_quality_issues WHERE issue_id=?", (row[0],)).fetchone()[0] != "RESOLVED"
    verified = verify_repair_batch(path, result["repair_batch_id"])
    assert verified["status"] == "VERIFIED"
    with connect(path) as db:
        assert db.execute("SELECT original_price FROM products WHERE official_sku='1001'").fetchone()[0] is None


def test_category_issue_is_routed_to_backlog_without_repair_candidate(tmp_path: Path):
    path = _db(tmp_path)
    with connect(path) as db:
        db.execute("UPDATE product_localizations SET cat2='' WHERE official_sku='1001' AND language='es'")
    result = build_repair_candidates(path)
    assert result["candidate_count"] == 0
    assert result["routed_category_count"] == 1
    with connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM category_backlog WHERE official_sku='1001'").fetchone()[0] == 1


def test_formal_correction_prepare_requires_approval_and_does_not_write_fact(tmp_path: Path):
    path = _db(tmp_path)
    with connect(path) as db:
        db.execute("UPDATE product_localizations SET details='<p>Texto</p>' WHERE official_sku='1001' AND language='es'")
    result = build_repair_candidates(path)
    prepared = prepare_formal_correction(path, result["repair_batch_id"])
    assert prepared["status"] == "PREPARED"
    assert prepared["official_fact_corrections"] == []
    candidate = DataQualityRepository(path).candidates(result["repair_batch_id"])[0]
    approve_candidate(path, candidate["candidate_id"], reviewer="human:alice")
    prepared = prepare_formal_correction(path, result["repair_batch_id"])
    assert prepared["real_primary_apply"] is False
    assert prepared["official_fact_corrections"]


def test_stale_base_commit_blocks_repair(tmp_path: Path):
    path = _db(tmp_path)
    result = build_repair_candidates(path, base_commit_id="old-head")
    for candidate in DataQualityRepository(path).candidates(result["repair_batch_id"]):
        approve_candidate(path, candidate["candidate_id"], reviewer="human:alice")
    with pytest.raises(Exception, match="STALE_BASE_COMMIT"):
        apply_repair_batch(path, result["repair_batch_id"], commit=True, actor="human:alice")


def test_source_hash_change_blocks_repair_and_preview_is_deterministic(tmp_path: Path):
    path = _db(tmp_path)
    with connect(path) as db:
        db.execute("UPDATE products SET original_price=current_price WHERE official_sku='1001'")
    result = build_repair_candidates(path)
    candidate = DataQualityRepository(path).candidates(result["repair_batch_id"])[0]
    preview = write_repair_preview(path, result["repair_batch_id"], tmp_path / "repair.csv")
    assert Path(preview["output_path"]).exists()
    with connect(path) as db:
        db.execute("UPDATE repair_candidates SET source_hash='expected-hash' WHERE candidate_id=?", (candidate["candidate_id"],))
    approve_candidate(path, candidate["candidate_id"], reviewer="human:alice")
    with pytest.raises(Exception, match="SOURCE_HASH_MISMATCH"):
        apply_repair_batch(path, result["repair_batch_id"], commit=True, actor="human:alice")


def test_repair_partial_failure_rolls_back_all_fixture_changes(tmp_path: Path):
    path = _db(tmp_path)
    with connect(path) as db:
        db.execute("UPDATE products SET original_price=current_price WHERE official_sku='1001'")
        db.execute("INSERT INTO products(canonical_id,official_sku,name_es,name_zh,current_price,original_price,source_hash,status,product_url,image_url) VALUES('ACT1002','1002','Producto2','商品2',2.0,2.0,'product-source-2','CURRENT','https://example/1002','https://example/image2')")
    result = build_repair_candidates(path)
    candidates = DataQualityRepository(path).candidates(result["repair_batch_id"])
    assert len(candidates) == 2
    for candidate in candidates:
        approve_candidate(path, candidate["candidate_id"], reviewer="human:alice")
    # Remove the later target so the first update is attempted and the second
    # fails; the transaction must restore the first product and statuses.
    missing_sku = candidates[-1]["official_sku"]
    with connect(path) as db:
        db.execute("DELETE FROM products WHERE official_sku=?", (missing_sku,))
    with pytest.raises(Exception, match="SOURCE_HASH_MISMATCH"):
        apply_repair_batch(path, result["repair_batch_id"], commit=True, actor="human:alice")
    with connect(path) as db:
        assert db.execute("SELECT original_price FROM products WHERE official_sku='1001'").fetchone()[0] == 1.0
    assert all(item["candidate_status"] == "APPROVED" for item in DataQualityRepository(path).candidates(result["repair_batch_id"]))


def test_primary_repair_is_forbidden(tmp_path: Path):
    path = _db(tmp_path)
    with connect(path) as db:
        db.execute("UPDATE schema_metadata SET value='PRIMARY' WHERE key='database_role'")
        db.execute("UPDATE products SET original_price=current_price WHERE official_sku='1001'")
    result = build_repair_candidates(path)
    for candidate in DataQualityRepository(path).candidates(result["repair_batch_id"]):
        approve_candidate(path, candidate["candidate_id"], reviewer="human:alice")
    with pytest.raises(Exception, match="REAL_PRIMARY_WRITE_FORBIDDEN"):
        apply_repair_batch(path, result["repair_batch_id"], commit=True, actor="human:alice")


def test_master_quality_clean_fixture_is_release_ready(tmp_path: Path):
    result = audit_master_quality(_db(tmp_path))
    assert result.release_ready is True
    assert result.status == "PASS"


def test_master_quality_dirty_fixture_is_blocked(tmp_path: Path):
    result = audit_master_quality(_db(tmp_path, clean=False))
    assert result.release_ready is False
    assert result.counts["INVALID_ORIGINAL_PRICE"] == 1
    assert result.counts["FIELD_PROVENANCE_MISSING"] == 1


def test_master_quality_current_scope_ignores_offline_localization_text(tmp_path: Path):
    path = _db(tmp_path)
    with connect(path) as db:
        db.execute("INSERT INTO products(canonical_id,official_sku,name_es,name_zh,current_price,original_price,status,product_url,image_url) VALUES('ACT2002','2002','Histórico','',1.0,1.0,'OFFLINE','https://example/2002','https://example/image2')")
        db.execute("INSERT INTO product_localizations(official_sku,language,name,cat1,cat2,spec,description,details,source,review_status,updated_at,source_hash,freshness_status) VALUES('2002','es','Histórico','Hogar','','1 unidad','<p>legacy</p>','<p>legacy</p>','official','APPROVED',CURRENT_TIMESTAMP,'old-hash','STALE')")
    result = audit_master_quality(path)
    assert not any("2002" in item for item in result.issues)
    assert result.records_checked == 1


def test_master_quality_blocks_text_and_orphan_history_but_keeps_category_warning_nonblocking(tmp_path: Path):
    path = _db(tmp_path)
    with connect(path) as db:
        db.execute("UPDATE product_localizations SET details='<p>bad</p>' WHERE official_sku='1001' AND language='es'")
        db.execute("INSERT INTO price_history(canonical_id,official_sku,observed_at,new_price,change_type) VALUES('X','ORPHAN','2026-01-01',1,'DOWN')")
        db.execute("INSERT INTO event_history(canonical_id,official_sku,occurred_at,event_type) VALUES('X','ORPHAN','2026-01-01','FIRST_SEEN')")
        db.execute("UPDATE product_localizations SET cat2='' WHERE official_sku='1001' AND language='zh'")
    result = audit_master_quality(path)
    assert result.release_ready is False
    assert result.counts["HTML_CONTAMINATION"] == 1
    assert result.counts["ORPHAN_FORMAL_PRICE_HISTORY"] == 1
    assert result.counts["ORPHAN_FORMAL_EVENT_HISTORY"] == 1
    assert result.counts["CATEGORY_MISSING"] == 1


def test_collection_healthy_run_is_ok():
    payload = {
        "sitemap_unique": 100, "listing_unique": 100, "current_valid": 100,
        "price_coverage": 1.0, "cat2_coverage": 1.0, "description_coverage": 1.0,
        "detail_failure_rate": 0.0, "category_coverage": {f"cat-{i}": True for i in range(15)},
    }
    result = evaluate_collection("r1", build_collection_metrics("r1", payload))
    assert result.state == "COLLECTION_OK"
    assert result.commit_allowed


def test_collection_missing_core_metrics_is_blocked_not_false_ok():
    result = evaluate_collection("r-unavailable", build_collection_metrics("r-unavailable", {}))
    assert result.state == "COLLECTION_BLOCKED"
    assert "LISTING_UNIQUE:UNAVAILABLE" in result.blockers
    assert "SUCCESSFUL_CATEGORY_COUNT:UNAVAILABLE" in result.blockers
    assert result.commit_allowed is False


def test_collection_preserves_per_category_counts_without_fabricating_missing_values():
    metrics = build_collection_metrics(
        "r-counts",
        {"category_coverage": {"cat-a": True}, "category_1_count": {"cat-a": 12, "cat-b": None}},
    )
    counts = {(item.metric_name, item.metric_scope): item for item in metrics if item.metric_name == "category_1_count"}
    assert counts[("category_1_count", "cat-a")].metric_value == 12
    assert counts[("category_1_count", "cat-a")].gate_status == "OK"
    assert counts[("category_1_count", "cat-b")].metric_value is None
    assert counts[("category_1_count", "cat-b")].gate_status == "UNAVAILABLE"


def test_schema_drift_evidence_contains_full_baseline_context():
    from action_tracker.data_quality.collection.drift import detect_schema_drift
    result = detect_schema_drift(
        {"cat2_coverage": 0.70},
        {"cat2_coverage": {"previous": 0.95, "median_7d": 0.94, "median_30d": 0.93}},
        run_id="r-drift",
        sample_skus={"cat2_coverage": ["1001"]},
    )
    assert result
    evidence = result[0].evidence
    assert evidence["previous_valid"] == 0.95
    assert evidence["baseline_7d"] == 0.94
    assert evidence["baseline_30d"] == 0.93
    assert evidence["sample_skus"] == ["1001"]


def test_schema_drift_carries_metric_source_hash_when_available():
    metrics = build_collection_metrics(
        "r-hash",
        {"cat2_coverage": 0.70, "source_hashes": {"cat2_coverage": "run-hash"}},
    )
    result = evaluate_collection(
        "r-hash", metrics,
        history=[
            {"run_id": "old", "metric_name": "cat2_coverage", "metric_scope": "", "metric_value": 0.95, "gate_status": "OK"},
        ],
    )
    assert result.drift_issues
    assert result.drift_issues[0].source_hash == "run-hash"
    assert result.drift_issues[0].evidence["source_hash"] == "run-hash"


def test_collection_metric_persistence_is_idempotent(tmp_path: Path):
    path = _db(tmp_path)
    repo = DataQualityRepository(path)
    metrics = build_collection_metrics("r1", {"listing_unique": 10})
    repo.save_metrics(metrics)
    repo.save_metrics(metrics)
    assert len(repo.get_metrics("r1")) == len(metrics)


def test_collection_metrics_persist_healthy_baselines_and_deltas(tmp_path: Path):
    path = _db(tmp_path)
    repo = DataQualityRepository(path)
    repo.save_metrics(build_collection_metrics("old", {"listing_unique": 100}))
    evaluate_and_persist(path, "new", {"listing_unique": 80})
    row = next(item for item in repo.get_metrics("new") if item["metric_name"] == "listing_unique")
    assert row["baseline_7d"] == 100.0
    assert row["baseline_30d"] == 100.0
    assert row["delta_7d"] == -20.0
    assert row["delta_30d"] == -20.0


def test_collection_retry_does_not_use_same_run_as_its_own_baseline(tmp_path: Path):
    path = _db(tmp_path)
    repo = DataQualityRepository(path)
    repo.save_metrics(build_collection_metrics("old", {"listing_unique": 100}))
    first = evaluate_and_persist(path, "retry", {"listing_unique": 80})
    second = evaluate_and_persist(path, "retry", {"listing_unique": 80})
    assert first.baselines["listing_unique"] == second.baselines["listing_unique"]
    row = next(item for item in repo.get_metrics("retry") if item["metric_name"] == "listing_unique")
    assert row["baseline_7d"] == 100.0


def test_master_quality_is_a_research_release_prerequisite():
    result = audit_research_release([], expected_skus=set(), master_quality={"release_ready": False})
    assert not result.ok
    assert "MASTER_QUALITY_BLOCKED" in result.issues


def test_primary_commit_evaluates_collection_integrity_before_write(tmp_path: Path):
    db_path = tmp_path / "primary.db"
    cfg = {"project_root": tmp_path, "storage": {"mode": "SQLITE_PRIMARY", "db_path": db_path}}
    migrate_v2(db_path, role="PRIMARY")
    healthy = {"sitemap_unique": 100, "listing_unique": 100, "current_valid": 100,
               "price_coverage": 1.0, "cat2_coverage": 1.0, "description_coverage": 1.0,
               "category_coverage": {f"cat-{i}": True for i in range(15)}}
    first_quality = evaluate_and_persist(db_path, "r1", {**healthy, "run_date": "2026-09-10"})
    healthy_with_quality = {**healthy, "collection_quality_state": first_quality.state,
                            "collection_metrics_hash": first_quality.metrics_hash}
    first = CommitBundle(run_id="r1", observation_date="2026-09-10", qa_state="PASS", run_record=healthy_with_quality,
                         collection_quality_state=first_quality.state,
                         requires_collection_integrity=True)
    commit_daily_bundle(cfg, first, mode="SQLITE_PRIMARY")
    degraded = {**healthy, "sitemap_unique": 70, "listing_unique": 70, "current_valid": 70}
    second_quality = evaluate_and_persist(db_path, "r2", {**degraded, "run_date": "2026-09-11"})
    degraded_with_quality = {**degraded, "collection_quality_state": second_quality.state,
                             "collection_metrics_hash": second_quality.metrics_hash}
    second = CommitBundle(run_id="r2", observation_date="2026-09-11", qa_state="PASS", run_record=degraded_with_quality,
                          collection_quality_state=second_quality.state,
                          requires_collection_integrity=True)
    with pytest.raises(ProductionDatabaseError, match="COLLECTION_QUALITY_BLOCKED"):
        commit_daily_bundle(cfg, second, mode="SQLITE_PRIMARY")


def test_collection_category_and_listing_drop_is_blocked():
    baseline = build_collection_metrics("old", {"listing_unique": 100, "current_valid": 100, "cat2_coverage": 1.0, "description_coverage": 1.0, "category_coverage": {f"cat-{i}": True for i in range(15)}})
    current = build_collection_metrics("new", {"listing_unique": 79, "current_valid": 79, "cat2_coverage": 0.80, "description_coverage": 0.70, "category_coverage": {f"cat-{i}": i < 14 for i in range(15)}})
    result = evaluate_collection("new", current, history=[metric.as_dict() for metric in baseline])
    assert result.state == "COLLECTION_BLOCKED"
    assert any("CATEGORY_SUCCESS" in item for item in result.blockers)
    assert result.drift_issues


def test_collection_semantic_drift_types_are_emitted():
    from action_tracker.data_quality.collection.drift import detect_schema_drift
    issues = detect_schema_drift(
        {"original_price_equals_current_ratio": 0.95, "ui_contamination_rate": 0.02, "html_contamination_rate": 0.03},
        {}, run_id="r-semantic",
    )
    assert {issue.issue_type for issue in issues} == {
        "PRICE_SCHEMA_DRIFT", "UI_CONTAMINATION_DRIFT", "HTML_CONTAMINATION_DRIFT",
    }


def test_degraded_runs_are_excluded_from_baseline():
    rows = [
        {"run_id": "bad", "metric_name": "__collection_state", "metric_scope": "COLLECTION_BLOCKED", "metric_value": None, "gate_status": "COLLECTION_BLOCKED"},
        {"run_id": "bad", "metric_name": "listing_unique", "metric_value": 1, "gate_status": "OK"},
        {"run_id": "good", "metric_name": "listing_unique", "metric_value": 100, "gate_status": "OK"},
    ]
    from action_tracker.data_quality.collection.baseline import calculate_baselines
    assert calculate_baselines(rows)["listing_unique"]["median_30d"] == 100


def test_calendar_baseline_excludes_current_day_and_uses_last_healthy_run_per_day():
    from action_tracker.data_quality.collection.baseline import calculate_baselines
    rows = [
        {"run_id": "old", "metric_name": "listing_unique", "metric_value": 10, "gate_status": "OK", "observation_date": "2026-08-01", "created_at": "2026-08-01T01:00:00Z"},
        {"run_id": "same-early", "metric_name": "listing_unique", "metric_value": 100, "gate_status": "OK", "observation_date": "2026-09-08", "created_at": "2026-09-08T01:00:00Z"},
        {"run_id": "same-late", "metric_name": "listing_unique", "metric_value": 120, "gate_status": "OK", "observation_date": "2026-09-08", "created_at": "2026-09-08T02:00:00Z"},
        {"run_id": "bad", "metric_name": "__collection_state", "metric_scope": "COLLECTION_BLOCKED", "metric_value": None, "gate_status": "COLLECTION_BLOCKED", "observation_date": "2026-09-09"},
        {"run_id": "bad", "metric_name": "listing_unique", "metric_value": 1, "gate_status": "OK", "observation_date": "2026-09-09"},
        {"run_id": "current", "metric_name": "listing_unique", "metric_value": 999, "gate_status": "OK", "observation_date": "2026-09-10"},
    ]
    result = calculate_baselines(rows, as_of="2026-09-10")
    assert result["listing_unique"]["previous"] == 120
    assert result["listing_unique"]["median_7d"] == 120
    assert result["listing_unique"]["median_30d"] == 120


def test_collection_override_is_bounded_and_auditable():
    from datetime import datetime, timedelta, timezone
    expires = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    evidence = {"actor": "operator", "reason": "incident review", "run_id": "r1", "metrics_hash": "h1", "expires_at": expires, "one_shot": True}
    assert validate_collection_override(evidence, run_id="r1", metrics_hash="h1")
    assert not validate_collection_override({**evidence, "one_shot": False}, run_id="r1", metrics_hash="h1")
    assert not validate_collection_override({**evidence, "metrics_hash": "other"}, run_id="r1", metrics_hash="h1")
    assert not collection_commit_allowed("COLLECTION_DEGRADED", override=True)
    assert collection_commit_allowed("COLLECTION_DEGRADED", override=True, override_evidence=evidence, run_id="r1", metrics_hash="h1")
