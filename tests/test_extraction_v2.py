from pathlib import Path

from action_tracker.database.connection import connect
from action_tracker.database.schema import migrate_v2
from action_tracker.extraction import ExtractionQuery, ExtractionService, SavedViewService, SelectionService
from action_tracker.delivery import ArtifactService


def _db(tmp_path: Path) -> Path:
    path = tmp_path / "action.db"; migrate_v2(path, role="PRIMARY")
    with connect(path) as db:
        db.execute("INSERT INTO products(canonical_id,official_sku,name_es,name_zh,current_price,original_price,status,product_url,first_seen_at,last_seen_at,last_checked_at) VALUES('A','1001','Producto Uno','商品一',1.5,2.0,'CURRENT','https://example.test/1001','2026-08-01','2026-08-30','2026-08-30')")
        db.execute("INSERT INTO products(canonical_id,official_sku,name_es,name_zh,current_price,status,product_url,first_seen_at,last_seen_at,last_checked_at) VALUES('B','1002','Producto Dos','商品二',3.0,'OFFLINE','https://example.test/1002','2026-08-01','2026-08-20','2026-08-30')")
        db.execute("INSERT INTO product_localizations(official_sku,language,name,cat1,cat2,spec,updated_at) VALUES('1001','zh','商品一','家居','清洁','1件','2026-08-30')")
        db.execute("INSERT INTO product_localizations(official_sku,language,name,cat1,cat2,spec,updated_at) VALUES('1002','zh','商品二','家居','收纳','2件','2026-08-30')")
        db.execute("INSERT INTO image_assets(official_sku,status,updated_at) VALUES('1001','AVAILABLE','2026-08-30')")
    return path


def test_extraction_is_deterministic_and_filters(tmp_path: Path):
    svc = ExtractionService(_db(tmp_path))
    q = ExtractionQuery(keyword="  商品一 ", min_price=1, max_price=2, has_original_price=True, limit=10)
    first = svc.execute(q); second = svc.execute(q)
    assert first.query_hash == second.query_hash
    assert first.matched_count == 1 and first.items[0]["official_sku"] == "1001"
    assert first.items[0]["has_image"] is True


def test_sort_pagination_and_offline(tmp_path: Path):
    svc = ExtractionService(_db(tmp_path))
    result = svc.execute(ExtractionQuery(statuses=("OFFLINE",), limit=1, offset=0, sort="current_price"))
    assert result.matched_count == 1 and result.items[0]["status"] == "OFFLINE"
    assert result.pagination["has_more"] is False


def test_extraction_supports_reappeared_and_image_ready_filters(tmp_path: Path):
    path = _db(tmp_path)
    with connect(path) as db:
        db.execute("INSERT INTO event_history(canonical_id,official_sku,occurred_at,event_type) VALUES('A','1001','2026-08-30','REAPPEARED')")
        db.execute("UPDATE image_assets SET master_image_path='1001.png',width=250,height=250 WHERE official_sku='1001'")
    svc = ExtractionService(path)
    assert svc.execute({"statuses": ["REAPPEARED"], "limit": 10}).matched_count == 1
    ready = svc.execute({"image_ready_for_export": True, "limit": 10})
    assert ready.matched_count == 1 and ready.items[0]["image_ready_for_export"] is True


def test_status_filters_are_union_including_reappeared(tmp_path: Path):
    path = _db(tmp_path)
    with connect(path) as db:
        db.execute("INSERT INTO products(canonical_id,official_sku,name_es,current_price,status,product_url,first_seen_at,last_seen_at,last_checked_at) VALUES('C','1003','Producto Tres',4.0,'CURRENT','https://example.test/1003','2026-08-01','2026-08-30','2026-08-30')")
        db.execute("INSERT INTO event_history(canonical_id,official_sku,occurred_at,event_type) VALUES('A','1001','2026-08-30','REAPPEARED')")
    result = ExtractionService(path).execute({"statuses": ["CURRENT", "REAPPEARED"], "limit": 10})
    assert result.matched_count == 2


def test_extraction_exposes_recent_event_and_historical_price_range(tmp_path: Path):
    path = _db(tmp_path)
    with connect(path) as db:
        db.execute("INSERT INTO price_history(canonical_id,official_sku,observed_at,old_price,new_price,change_type) VALUES('A','1001','2026-08-20',1.0,1.5,'UP')")
        db.execute("INSERT INTO price_history(canonical_id,official_sku,observed_at,old_price,new_price,change_type) VALUES('A','1001','2026-08-30',1.5,1.2,'DOWN')")
    result = ExtractionService(path).execute({"sort": "recent_change", "descending": True, "limit": 10})
    item = next(row for row in result.items if row["official_sku"] == "1001")
    assert item["change_direction"] == "DOWN" and item["historical_low"] == 1.0 and item["historical_high"] == 1.5


def test_latest_price_is_one_deterministic_row_per_sku(tmp_path: Path):
    path = _db(tmp_path)
    with connect(path) as db:
        db.execute("INSERT INTO price_history(canonical_id,official_sku,observed_at,old_price,new_price,change_type) VALUES('A','1001','2026-08-30',1.0,1.5,'UP')")
        db.execute("INSERT INTO price_history(canonical_id,official_sku,observed_at,old_price,new_price,change_type) VALUES('A','1001','2026-08-30',1.5,1.2,'DOWN')")
    result = ExtractionService(path).execute({"skus": ["1001"], "limit": 10})
    assert result.matched_count == 1 and len(result.items) == 1
    assert result.items[0]["change_direction"] == "DOWN"


def test_event_ranges_recent_offline_reappeared_and_historical_filters(tmp_path: Path):
    path = _db(tmp_path)
    with connect(path) as db:
        db.execute("INSERT INTO lifecycle_state(official_sku,canonical_id,current_status,offline_date,last_state_observation_date,updated_at) VALUES('1002','B','OFFLINE','2026-08-30','2026-08-30','2026-08-30')")
        db.execute("INSERT INTO event_history(canonical_id,official_sku,occurred_at,event_type) VALUES('A','1001','2026-08-30','REAPPEARED')")
        db.execute("INSERT INTO price_history(canonical_id,official_sku,observed_at,old_price,new_price,change_type) VALUES('A','1001','2026-08-20',1.5,1.0,'DOWN')")
        db.execute("INSERT INTO price_history(canonical_id,official_sku,observed_at,old_price,new_price,change_type) VALUES('A','1001','2026-08-30',1.0,1.4,'UP')")
    svc = ExtractionService(path)
    assert svc.execute({"event_types": ["REAPPEARED"], "event_from": "2026-08-30", "event_to": "2026-08-30", "limit": 10}).matched_count == 1
    assert svc.execute({"event_types": ["REAPPEARED"], "event_from": "2026-08-01", "event_to": "2026-08-29", "limit": 10}).matched_count == 0
    assert svc.execute({"statuses": ["OFFLINE"], "event_types": ["OFFLINE"], "event_last_n_days": 7, "limit": 10}).matched_count == 1
    assert svc.execute({"historical_low_max": 1.0, "limit": 10}).matched_count == 1
    assert svc.execute({"historical_high_min": 1.4, "limit": 10}).matched_count == 1


def test_extraction_contract_time_dimensions_and_canonical_id(tmp_path: Path):
    path = _db(tmp_path)
    with connect(path) as db:
        db.execute("INSERT INTO event_history(canonical_id,official_sku,occurred_at,event_type) VALUES('A','1001','2026-08-30','PRICE_DOWN')")
    svc = ExtractionService(path)
    assert ExtractionQuery().normalized()["statuses"] == ExtractionQuery.from_dict({}).normalized()["statuses"] == ["current"]
    assert svc.execute({"canonical_id": "A", "first_seen_from": "2026-08-01", "first_seen_to": "2026-08-01"}).matched_count == 1
    assert svc.execute({"last_seen_from": "2026-08-30", "last_seen_to": "2026-08-30"}).matched_count == 1
    recent = svc.execute({"event_types": ["price_down"], "event_last_n_days": 7})
    assert recent.matched_count == 1


def test_extraction_six_field_localization_and_specific_missing_field(tmp_path: Path):
    path = _db(tmp_path)
    with connect(path) as db:
        db.execute("INSERT INTO products(canonical_id,official_sku,name_es,current_price,status,product_url,first_seen_at,last_seen_at,last_checked_at) VALUES('C','1003','Producto Tres',4.0,'CURRENT','https://example.test/1003','2026-08-01','2026-08-30','2026-08-30')")
        db.execute("INSERT INTO product_localizations(official_sku,language,name,cat1,cat2,spec,description,details,updated_at) VALUES('1003','zh','商品三','家居','收纳','3件','描述','详情','2026-08-30')")
        db.execute("UPDATE product_localizations SET description='描述',details='详情' WHERE official_sku='1002' AND language='zh'")
    svc = ExtractionService(path)
    assert svc.execute({"localization_status": "COMPLETE", "limit": 10}).matched_count == 1
    missing = svc.execute({"missing_fields": ["desc_zh"], "limit": 10})
    assert missing.matched_count == 1 and missing.items[0]["official_sku"] == "1001"


def test_localization_freshness_is_read_and_stale_overrides_complete(tmp_path: Path):
    path = _db(tmp_path)
    with connect(path) as db:
        db.execute("UPDATE product_localizations SET description='描述',details='详情',freshness_status='STALE' WHERE official_sku='1001' AND language='zh'")
        db.execute("INSERT INTO products(canonical_id,official_sku,name_es,current_price,status) VALUES('C','1003','Producto Tres',4.0,'CURRENT')")
        db.execute("INSERT INTO product_localizations(official_sku,language,name,cat1,cat2,spec,description,details,freshness_status,updated_at) VALUES('1003','zh','商品三','家居','收纳','3件','描述','详情','CURRENT','2026-08-30')")
    result = ExtractionService(path).execute({"localization_status": "STALE", "limit": 10})
    assert result.matched_count == 1 and result.items[0]["official_sku"] == "1001"
    assert result.items[0]["zh_freshness_status"] == "STALE"
    complete = ExtractionService(path).execute({"localization_status": "COMPLETE", "limit": 10})
    assert complete.matched_count == 1 and complete.items[0]["official_sku"] == "1003"


def test_localization_status_incomplete_and_review_states(tmp_path: Path):
    path = _db(tmp_path)
    with connect(path) as db:
        db.execute("UPDATE product_localizations SET freshness_status='CURRENT' WHERE official_sku='1001' AND language='zh'")
        db.execute("UPDATE product_localizations SET description='描述',details='详情',review_status='BLOCKED' WHERE official_sku='1001' AND language='zh'")
        db.execute("UPDATE product_localizations SET review_status='PENDING' WHERE official_sku='1002' AND language='zh'")
    svc = ExtractionService(path)
    assert svc.execute({"localization_status": "BLOCKED", "limit": 10}).matched_count == 1
    assert svc.execute({"localization_status": "PENDING", "statuses": ["OFFLINE"], "limit": 10}).matched_count == 1
    assert svc.execute({"localization_status": "INCOMPLETE", "limit": 10}).matched_count == 0


def test_saved_view_dynamic_and_selection_membership_fixed(tmp_path: Path):
    path = _db(tmp_path); view = SavedViewService(path); selection = SelectionService(path)
    saved = view.create("当前商品", {"statuses": ["CURRENT"], "limit": 100})
    selected = selection.create("本次选择", saved["query"], view_id=saved["view_id"])
    assert selected["members"] == ["1001"]
    with connect(path) as db:
        db.execute("UPDATE products SET current_price=9.9 WHERE official_sku='1001'")
    refreshed = ExtractionService(path).execute(ExtractionQuery(statuses=("CURRENT",)))
    assert refreshed.items[0]["current_price"] == 9.9
    assert selection.get(selected["selection_id"])["members"] == ["1001"]


def test_saved_view_update_and_delete_are_explicit(tmp_path: Path):
    path = _db(tmp_path); view = SavedViewService(path)
    saved = view.create("临时视图", {"statuses": ["CURRENT"]})
    updated = view.update(saved["view_id"], name="当前低价", query={"statuses": ["CURRENT"], "max_price": 2})
    assert updated["name"] == "当前低价" and updated["query"]["max_price"] == 2
    view.delete(saved["view_id"])
    assert view.get(saved["view_id"]) is None


def test_selection_ignores_page_limit_and_offset_for_membership(tmp_path: Path):
    path = _db(tmp_path)
    with connect(path) as db:
        db.execute("INSERT INTO products(canonical_id,official_sku,name_es,current_price,status,product_url,first_seen_at,last_seen_at,last_checked_at) VALUES('C','1003','Producto Tres',4.0,'CURRENT','https://example.test/1003','2026-08-01','2026-08-30','2026-08-30')")
    selected = SelectionService(path).create("完整选择", {"statuses": ["CURRENT"], "limit": 1, "offset": 1})
    assert selected["matched_count"] == 2 and selected["members"] == ["1001", "1003"]


def test_image_zip_records_membership_and_missing_report(tmp_path: Path):
    path = _db(tmp_path); selection = SelectionService(path).create("图包", {"statuses":["CURRENT"],"limit":100})
    image_root = tmp_path / "images"; image_root.mkdir(); (image_root / "1001.png").write_bytes(b"png")
    result = ArtifactService(path).build_image_zip(selection["selection_id"], image_root, tmp_path / "images.zip")
    assert result["included"] == 1 and result["missing"] == 0


def test_selection_csv_preserves_member_after_offline_transition_and_history(tmp_path: Path):
    path = _db(tmp_path)
    with connect(path) as db:
        db.execute("INSERT INTO runs(run_id,run_date,status,qa_state,dry_run) VALUES('run-csv','2026-08-30','COMMITTED','PASS',0)")
        db.execute("INSERT INTO commit_batches(commit_id,run_id,bundle_hash,schema_version,started_at,committed_at,status) VALUES('commit-csv','run-csv','hash','2.0.0','2026-08-30','2026-08-30','COMMITTED')")
    selection = SelectionService(path).create("CSV", {"statuses": ["CURRENT"], "limit": 100})
    with connect(path) as db:
        db.execute("UPDATE products SET status='OFFLINE' WHERE official_sku='1001'")
    service = ArtifactService(path)
    first = service.build_csv(selection["selection_id"], tmp_path / "selection.csv")
    second = service.build_csv(selection["selection_id"], tmp_path / "selection.csv")
    assert first["missing"] == [] and second["missing"] == []
    import csv
    with (tmp_path / "selection.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["official_sku"] == "1001" and rows[0]["status"] == "OFFLINE"
    history = service.list(selection["selection_id"])
    assert len(history) == 2 and history[0]["artifact_id"] != history[1]["artifact_id"]
    assert all(item["source_commit_id"] == "commit-csv" and item["selection_source_commit_id"] == "commit-csv" for item in history)


def test_selection_artifact_manifests_are_per_generation_file(tmp_path: Path):
    path = _db(tmp_path)
    selection = SelectionService(path).create("Manifest", {"statuses": ["CURRENT"]})
    image_root = tmp_path / "images"; image_root.mkdir(); (image_root / "1001.png").write_bytes(b"png")
    service = ArtifactService(path)
    csv_result = service.build_csv(selection["selection_id"], tmp_path / "selection.csv")
    zip_result = service.build_image_zip(selection["selection_id"], image_root, tmp_path / "selection.zip")
    csv_artifact, zip_artifact = service.list(selection["selection_id"])
    assert csv_result["missing"] == [] and zip_result["missing"] == 0
    assert csv_artifact["manifest_path"] != zip_artifact["manifest_path"]
    assert Path(csv_artifact["manifest_path"]).exists() and Path(zip_artifact["manifest_path"]).exists()
