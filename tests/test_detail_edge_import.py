import json
from pathlib import Path

import openpyxl
import pytest

from action_tracker.database.connection import connect
from action_tracker.database.production import (
    CommitBundle, ProductionWriter, ProductionDatabaseError, apply_detail_only_updates,
    apply_verified_listing_reconciliation,
)
from action_tracker.excel.reader import ES_MAP
from action_tracker.orchestrator.detail_edge_import import (
    EDGE_STAGING_HEADERS,
    EdgeDetailImportError,
    _excel_date,
    _already_enriched_skus,
    _load_deferred_detail_queue,
    _normalize_detail_delimiters,
    _normalize_description,
    _validate_master_es_schema,
    _validate_records,
)
from action_tracker.orchestrator.edge_listing_reconcile import _build_deferred_category_plan
from action_tracker.services.normalization import normalize_official_text


def _primary_db(path: Path) -> None:
    bundle = CommitBundle(
        run_id="2026-09-03_000001", observation_date="2026-09-03", qa_state="PASS",
        current_products=({"sku": "2571395", "name_es": "Viejo", "current_price": 6.95,
                           "status": "CURRENT", "product_url": "https://www.action.com/es-es/p/2571395/old/"},),
        localization_updates=(
            {"sku": "2571395", "language": "es", "name": "Viejo", "cat1": "Vivienda", "spec": "20 metros"},
            {"sku": "2571395", "language": "zh", "name": "旧名称"},
        ),
        lifecycle_updates=({"sku": "2571395", "current_status": "ACTIVE", "last_run_id": "2026-09-03_000001"},),
        observations=({"run_id": "2026-09-03_000001", "sku": "2571395", "observation_date": "2026-09-03",
                       "presence_state": "PRESENT", "observation_complete": True, "absence_capable": True},),
    )
    ProductionWriter(path, role="PRIMARY").commit(bundle)
    with connect(path) as db:
        db.execute("UPDATE products SET status='CURRENT' WHERE official_sku='2571395'")
        db.execute("UPDATE lifecycle_state SET current_status='ACTIVE' WHERE official_sku='2571395'")


def test_detail_only_writer_never_changes_listing_facts_price_or_lifecycle(tmp_path: Path):
    db = tmp_path / "action.db"
    _primary_db(db)
    result = apply_detail_only_updates(db, [{
        "sku": "2571395", "name_es": "Iluminación navideña multicolor",
        "cat1_es": "Decoración", "cat2_es": "Navidad", "spec_es": "240 luces LED | 20 metros",
        "desc_es": "Para interior y exterior", "details_es": "Número de pilas necesarias: 2",
        "product_url": "https://action.com/es-es/p/2571395/iluminacion-navidena-multicolor/",
    }], import_id="edge-import-test", evidence={"source": "EDGE_PLUGIN"})
    assert result["changed_skus"] == 1
    with connect(db) as conn:
        product = conn.execute("SELECT current_price,status,product_url,name_es FROM products WHERE official_sku='2571395'").fetchone()
        loc = conn.execute("SELECT name,cat1,cat2,spec,description,details,source FROM product_localizations WHERE official_sku='2571395' AND language='es'").fetchone()
    assert tuple(product[:2]) == (6.95, "CURRENT")
    assert product[2].endswith("/old/")
    assert product[3] == "Viejo"
    assert tuple(loc[:4]) == ("Viejo", "Vivienda", None, "20 metros")
    assert loc[4] == "Para interior y exterior" and loc[5] == "Número de pilas necesarias: 2"
    assert loc[6] == "EDGE_PLUGIN"
    with connect(db) as conn:
        field_rows = conn.execute(
            "SELECT field_name,value FROM localization_fields WHERE official_sku='2571395' AND language='es'"
        ).fetchall()
        canonical_rows = conn.execute(
            "SELECT field_name,value FROM localization_field_provenance WHERE official_sku='2571395' AND language='es'"
        ).fetchall()
        assert dict(field_rows)["description"] == "Para interior y exterior"
        assert dict(field_rows)["details"] == "Número de pilas necesarias: 2"
        assert dict(canonical_rows)["details"] == "Número de pilas necesarias: 2"
        assert conn.execute("SELECT COUNT(*) FROM product_fact_versions WHERE official_sku='2571395'").fetchone()[0] >= 2
        assert conn.execute("SELECT COUNT(*) FROM localization_patch_events").fetchone()[0] >= 4


def test_detail_writer_canonicalizes_only_existing_legacy_action_host(tmp_path: Path):
    db = tmp_path / "action.db"
    _primary_db(db)
    with connect(db) as conn:
        conn.execute("UPDATE products SET product_url='https://action.com/es-es/p/2571395/old/' WHERE official_sku='2571395'")
    apply_detail_only_updates(db, [{
        "sku": "2571395", "product_url": "https://action.com/es-es/p/2571395/untrusted/",
        "desc_es": "Descripción verificada", "details_es": "Detalles verificados",
    }], import_id="edge-import-url-repair")
    with connect(db) as conn:
        url = conn.execute("SELECT product_url FROM products WHERE official_sku='2571395'").fetchone()[0]
    assert url == "https://www.action.com/es-es/p/2571395/old/"


def test_verified_listing_reconciliation_only_updates_categories_badge_and_blank_first_seen(tmp_path: Path):
    db = tmp_path / "action.db"
    _primary_db(db)
    with connect(db) as conn:
        conn.execute(
            "UPDATE products SET raw_badges='Nuevo',action_new_badge=0,first_seen_at=NULL "
            "WHERE official_sku='2571395'"
        )
        conn.execute(
            "UPDATE lifecycle_state SET first_seen_date='2026-08-30' WHERE official_sku='2571395'"
        )
    result = apply_verified_listing_reconciliation(db, [{
        "sku": "2571395", "cat1_es": "Hogar", "cat2_es": "Limpieza",
        "action_new_badge": True, "first_seen": "2026-08-30",
    }], import_id="edge-listing-test")
    assert result == {
        "status": "APPLIED", "import_id": "edge-listing-test", "rows": 1, "changed_skus": 1,
        "category_fields_changed": 2, "badge_fields_changed": 1, "first_seen_fields_hydrated": 1,
    }
    with connect(db) as conn:
        product = conn.execute(
            "SELECT current_price,status,action_new_badge,first_seen_at FROM products WHERE official_sku='2571395'"
        ).fetchone()
        loc = conn.execute(
            "SELECT name,cat1,cat2,spec,description,details,cat1_source,cat2_source "
            "FROM product_localizations WHERE official_sku='2571395' AND language='es'"
        ).fetchone()
    assert tuple(product) == (6.95, "CURRENT", 1, "2026-08-30")
    assert tuple(loc[:6]) == ("Viejo", "Hogar", "Limpieza", "20 metros", None, None)
    assert tuple(loc[6:]) == ("official_breadcrumb", "official_breadcrumb")


def test_listing_reconciliation_rejects_badge_not_backed_by_raw_listing_tag(tmp_path: Path):
    db = tmp_path / "action.db"
    _primary_db(db)
    with connect(db) as conn:
        conn.execute("UPDATE lifecycle_state SET first_seen_date='2026-08-30' WHERE official_sku='2571395'")
    with pytest.raises(ProductionDatabaseError, match="BADGE_NOT_RAW_TAG"):
        apply_verified_listing_reconciliation(db, [{
            "sku": "2571395", "cat1_es": "Hogar", "cat2_es": "Limpieza",
            "action_new_badge": True, "first_seen": "2026-08-30",
        }], import_id="edge-listing-test")


def test_edge_import_rejects_challenge_and_mojibake():
    current = {"2571395": {"sku": "2571395"}}
    base = {"sku": "2571395", "product_url": "https://www.action.com/es-es/p/2571395/x/",
            "desc_es": "Descripción", "details_es": "Detalles"}
    with pytest.raises(EdgeDetailImportError, match="PAGE_NOT_VERIFIED"):
        _validate_records({}, [{**base, "page_title": "请稍候…"}], {"2571395"}, current)
    with pytest.raises(EdgeDetailImportError, match="ENCODING_ERROR"):
        _validate_records({}, [{**base, "details_es": "坏�数据"}], {"2571395"}, current)


def test_edge_import_does_not_treat_ordinary_un_momento_as_challenge():
    current = {"2571395": {"sku": "2571395"}}
    row = {
        "sku": "2571395",
        "product_url": "https://www.action.com/es-es/p/2571395/x/",
        "page_title": "Producto de bienestar | Action ES",
        "body_sample": "Disfruta de un momento de relajación en casa.",
        "desc_es": "Disfruta de un momento de relajación.",
        "details_es": "Número del artículo\t2571395",
    }
    result = _validate_records({}, [row], {"2571395"}, current)
    assert result[0]["sku"] == "2571395"


def test_edge_import_rejects_explicit_spanish_security_interstitial():
    current = {"2571395": {"sku": "2571395"}}
    row = {
        "sku": "2571395",
        "product_url": "https://www.action.com/es-es/p/2571395/x/",
        "page_title": "Un momento…",
        "body_sample": "Verificación de seguridad. Ray ID: abc",
        "desc_es": "Descripción",
        "details_es": "Detalles",
    }
    with pytest.raises(EdgeDetailImportError, match="PAGE_NOT_VERIFIED"):
        _validate_records({}, [row], {"2571395"}, current)


def test_staging_normalizers_remove_heading_and_all_detail_separators():
    assert _normalize_description("Descripción\nLínea uno\nLínea dos") == "Línea uno\nLínea dos"
    assert _normalize_detail_delimiters("Color\tRojo | Cantidad\t5 piezas") == "Color: Rojo; Cantidad: 5 piezas"


def test_official_text_normalizer_removes_transport_pollution_only():
    assert normalize_official_text("Añadir a tus favoritos", field="spec") is None
    assert normalize_official_text("Descripción\n<a href='x'>Texto</a>\nLeer más", field="description") == "Texto"
    assert normalize_official_text("Material:: Plástico;  Número: 2", field="details") == "Material: Plástico; Número: 2"


def test_detail_normalizer_repairs_split_official_labels_without_deduplicating():
    value = normalize_official_text(
        "Longitud del cable:; 20 m; Longitud del cable:; 20; Método de fijación; Colgado; Número del artículo; 2536376",
        field="details",
    )
    assert value == (
        "Longitud del cable: 20 m; Longitud del cable: 20; "
        "Método de fijación: Colgado; Número del artículo: 2536376"
    )


def test_edge_import_requires_source_url_sku_match():
    current = {"2571395": {"sku": "2571395"}}
    row = {"sku": "2571395", "product_url": "https://www.action.com/es-es/p/2536661/x/",
           "desc_es": "Descripción", "details_es": "Detalles"}
    with pytest.raises(EdgeDetailImportError, match="URL_SKU_MISMATCH"):
        _validate_records({}, [row], {"2571395"}, current)


def test_blocked_recovery_allows_unplanned_current_sku_when_details_missing():
    current = {"2571395": {"sku": "2571395", "description_es": None, "details_es": None}}
    row = {"sku": "2571395", "product_url": "https://www.action.com/es-es/p/2571395/x/",
           "desc_es": "Descripción", "details_es": "Detalles"}
    result = _validate_records({}, [row], set(), current)
    assert result[0]["sku"] == "2571395"


def test_edge_import_still_rejects_unplanned_sku_with_existing_details():
    current = {"2571395": {"sku": "2571395", "description_es": "Descripción", "details_es": "Detalles"}}
    row = {"sku": "2571395", "product_url": "https://www.action.com/es-es/p/2571395/x/",
           "desc_es": "Nueva descripción", "details_es": "Nuevos detalles"}
    with pytest.raises(EdgeDetailImportError, match="SKU_NOT_PLANNED"):
        _validate_records({}, [row], set(), current)


def test_staging_validation_can_review_existing_detail_without_writing_it():
    current = {"2571395": {"sku": "2571395", "description_es": "Descripción", "details_es": "Detalles"}}
    row = {"sku": "2571395", "product_url": "https://www.action.com/es-es/p/2571395/x/",
           "desc_es": "Nueva descripción", "details_es": "Nuevos detalles"}
    result = _validate_records({}, [row], set(), current, allow_already_enriched=True)
    assert result[0]["sku"] == "2571395"


def test_staging_validation_keeps_incomplete_page_for_followup():
    current = {"2571395": {"sku": "2571395", "description_es": None, "details_es": None}}
    row = {"sku": "2571395", "product_url": "https://www.action.com/es-es/p/2571395/x/",
           "desc_es": "Descripción", "details_es": ""}
    result = _validate_records({}, [row], set(), current, allow_incomplete=True)
    assert result[0]["sku"] == "2571395"


def test_master_aligned_details_normalize_tabs_and_newlines():
    assert _normalize_detail_delimiters("Color\tBlanco\nPotencia\t3.6") == "Color: Blanco; Potencia: 3.6"


def test_staging_schema_is_exact_master_es_schema():
    assert EDGE_STAGING_HEADERS == list(ES_MAP)


def test_staging_dates_are_excel_date_values():
    assert _excel_date("2026-09-03").isoformat() == "2026-09-03"
    with pytest.raises(EdgeDetailImportError, match="DATE_INVALID"):
        _excel_date("not-a-date")


def test_staging_rejects_master_header_drift(tmp_path: Path):
    path = tmp_path / "master.xlsx"
    workbook = openpyxl.Workbook()
    ws = workbook.active
    ws.title = "02_SKU_ES_CURRENT"
    ws.append([*EDGE_STAGING_HEADERS[:-1], "错误字段"])
    workbook.save(path)
    workbook.close()
    with pytest.raises(EdgeDetailImportError, match="SCHEMA_MISMATCH"):
        _validate_master_es_schema(path)


def test_deferred_queue_prefers_authoritative_detail_backlog(tmp_path: Path):
    (tmp_path / "detail_backlog.csv").write_text(
        "sku,queue_status,reason\n"
        "3220001,DEFERRED,NEW\n"
        "3220002,deferred,REAPPEARED\n"
        "3220003,READY,NEW\n",
        encoding="utf-8",
    )
    (tmp_path / "product_updates.csv").write_text(
        "sku,reason,detail_selected\n2570001,MISSING_FIELD,0\n",
        encoding="utf-8",
    )
    queue, source = _load_deferred_detail_queue(tmp_path)
    assert queue == {"3220001", "3220002"}
    assert source == "detail_backlog"


def test_deferred_queue_uses_legacy_missing_field_fallback(tmp_path: Path):
    (tmp_path / "product_updates.csv").write_text(
        "sku,reason,detail_selected\n"
        "2570001,MISSING_FIELD,0\n"
        "2570002,MISSING_FIELD,false\n"
        "2570003,MISSING_FIELD,1\n"
        "2570004,NEW,0\n",
        encoding="utf-8",
    )
    queue, source = _load_deferred_detail_queue(tmp_path)
    assert queue == {"2570001", "2570002"}
    assert source == "product_updates:MISSING_FIELD"


def test_deferred_import_rejects_partial_or_complete_existing_details():
    current = {
        "3220001": {"description_es": "Descripción", "details_es": None},
        "3220002": {"description_es": None, "details_es": "Detalles"},
        "3220003": {"description_es": "Descripción", "details_es": "Detalles"},
        "3220004": {"description_es": None, "details_es": None},
    }
    assert _already_enriched_skus(set(current), current) == ["3220001", "3220002", "3220003"]


def test_deferred_category_plan_fills_blank_categories_without_overwriting_facts():
    current = {
        "3220001": {"sku": "3220001", "cat1_es": "Hogar", "cat2_es": None,
                    "raw_tags": "", "first_seen": "2026-09-01"},
    }
    lifecycle = {"3220001": {"first_seen_date": "2026-09-01"}}
    details = [{"sku": "3220001", "product_url": "https://www.action.com/es-es/p/3220001/x/",
                "cat1_es": "Hogar", "cat2_es": "Almacenaje"}]
    plan, conflicts = _build_deferred_category_plan(
        current=current, lifecycle=lifecycle, detail_records=details,
    )
    assert conflicts == []
    assert plan[0]["cat1_es"] == "Hogar"
    assert plan[0]["cat2_es"] == "Almacenaje"


def test_deferred_category_plan_isolates_non_blank_category_conflicts():
    current = {
        "3220001": {"sku": "3220001", "cat1_es": "Cocina", "cat2_es": None,
                    "raw_tags": "", "first_seen": "2026-09-01"},
    }
    lifecycle = {"3220001": {"first_seen_date": "2026-09-01"}}
    details = [{"sku": "3220001", "product_url": "https://www.action.com/es-es/p/3220001/x/",
                "cat1_es": "Vivienda", "cat2_es": "Muebles"}]
    plan, conflicts = _build_deferred_category_plan(
        current=current, lifecycle=lifecycle, detail_records=details,
    )
    assert plan == []
    assert conflicts[0]["sku"] == "3220001"
    assert conflicts[0]["fields"] == ["cat1_es"]


def test_deferred_category_plan_accepts_only_explicitly_approved_official_conflict():
    current = {
        "3220001": {"sku": "3220001", "cat1_es": "Cocina", "cat2_es": None,
                    "raw_tags": "", "first_seen": "2026-09-01"},
    }
    lifecycle = {"3220001": {"first_seen_date": "2026-09-01"}}
    details = [{"sku": "3220001", "product_url": "https://www.action.com/es-es/p/3220001/x/",
                "cat1_es": "Vivienda", "cat2_es": "Muebles"}]
    plan, conflicts = _build_deferred_category_plan(
        current=current, lifecycle=lifecycle, detail_records=details,
        approved_conflict_skus={"3220001"},
    )
    assert conflicts == []
    assert plan[0]["cat1_es"] == "Vivienda"
    assert plan[0]["cat2_es"] == "Muebles"
    assert plan[0]["official_conflict_approved"] is True
