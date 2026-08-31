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


def test_extraction_exposes_recent_event_and_historical_price_range(tmp_path: Path):
    path = _db(tmp_path)
    with connect(path) as db:
        db.execute("INSERT INTO price_history(canonical_id,official_sku,observed_at,old_price,new_price,change_type) VALUES('A','1001','2026-08-20',1.0,1.5,'UP')")
        db.execute("INSERT INTO price_history(canonical_id,official_sku,observed_at,old_price,new_price,change_type) VALUES('A','1001','2026-08-30',1.5,1.2,'DOWN')")
    result = ExtractionService(path).execute({"sort": "recent_change", "descending": True, "limit": 10})
    item = next(row for row in result.items if row["official_sku"] == "1001")
    assert item["change_direction"] == "DOWN" and item["historical_low"] == 1.2 and item["historical_high"] == 1.5


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


def test_image_zip_records_membership_and_missing_report(tmp_path: Path):
    path = _db(tmp_path); selection = SelectionService(path).create("图包", {"statuses":["CURRENT"],"limit":100})
    image_root = tmp_path / "images"; image_root.mkdir(); (image_root / "1001.png").write_bytes(b"png")
    result = ArtifactService(path).build_image_zip(selection["selection_id"], image_root, tmp_path / "images.zip")
    assert result["included"] == 1 and result["missing"] == 0
