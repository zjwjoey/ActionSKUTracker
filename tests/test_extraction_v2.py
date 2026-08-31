from pathlib import Path

from action_tracker.database.connection import connect
from action_tracker.database.schema import migrate_v2
from action_tracker.extraction import ExtractionQuery, ExtractionService, SavedViewService, SelectionService


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
