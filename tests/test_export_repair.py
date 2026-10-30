from action_tracker.exporting.repair import ExportRepairEngine
from action_tracker.exporting.repair_overrides import HEADERS, field_source_hash, load_overrides
from action_tracker.exporting.repair_report import (
    ExportRepairReport,
    _numeric_addition_explained,
    _source_bound_alias,
    audit_repaired_rows,
)
from action_tracker.exporting.dictionary_join import _repair_export_description, _repair_export_details, _translate_official_tags, _zh_remarks
from action_tracker.exporting.repair_rules import repair_details, repair_spec, repair_title
from action_tracker.exporting.repair_tokens import repair_source_model_fragments
import csv
import json
from pathlib import Path
from copy import deepcopy
import pytest


def _record():
    return {
        "sku": "1001",
        "name_es": "Producto A4",
        "cat1_es": "Hogar",
        "cat2_es": "Limpieza",
        "spec_es": "300 gramos",
        "unit_price": "1 €/ud.",
        "desc_es": "Producto de limpieza",
        "details_es": "Material plástico",
    }


def _row(**values):
    row = {
        "编号": "1001", "标题": "商品 A4", "分类1": "家居布置", "分类2": "清洁用品",
        "规格": "300克", "单价": "1 €/件", "描述": "清洁产品", "产品详情": "塑料材质",
    }
    row.update(values)
    return row


def test_repair_report_records_before_after_and_is_idempotent():
    report = ExportRepairReport(run_id="run-1")
    engine = ExportRepairEngine(report=report)
    record = _record()
    first = engine.apply(
        sku="1001", field="spec_zh", value="300 gramos", source_field="spec_es",
        source=record["spec_es"], source_hash="hash", rule="SPEC_SOURCE_FACTS",
        repairer=repair_spec,
    )
    second = repair_spec(first, record["spec_es"])
    assert first == "300克"
    assert second == first
    assert len(report.events) == 1
    event = report.events[0].as_dict()
    assert event["before"] == "300 gramos"
    assert event["after"] == "300克"
    assert event["rule_version"] == "1.0"


def test_repaired_row_audit_deduplicates_findings_to_field_rate():
    records = [_record()]
    rows = [_row(标题="商品")]
    result = audit_repaired_rows(records, rows)
    assert result["eligible_fields"] == 6
    assert result["affected_fields"] == 1
    assert result["affected_skus"] == 1
    assert 0 < result["field_pass_rate"] < 1
    assert result["field_error_rate"] == 1 - result["field_pass_rate"]


def test_export_audit_allows_source_bound_brand_but_not_ordinary_spanish():
    record = {**_record(), "name_es": "Producto Alison & Mae", "desc_es": "Marca Alison & Mae"}
    row = _row(标题="商品 Alison & Mae", 描述="来自 Alison & Mae")
    result = audit_repaired_rows([record], [row])
    assert not any(item["code"] == "SPANISH_RESIDUAL" for item in result["findings"])
    ordinary = audit_repaired_rows([record], [_row(描述="Producto Alison & Mae")])
    assert any(item["code"] == "SPANISH_RESIDUAL" for item in ordinary["findings"])


def test_ordinary_hyphenated_spanish_is_never_source_bound_by_shape():
    for residual in ("Extra-Fuerte", "Super-Limpio", "Producto-Pro", "Material-X"):
        record = {**_record(), "desc_es": residual}
        result = audit_repaired_rows([record], [_row(描述=residual)])
        assert any(
            item["code"] == "SPANISH_RESIDUAL" and item["field"] == "desc_zh"
            for item in result["findings"]
        ), residual


def test_export_audit_ignores_digit_inside_confirmed_brand_removed_from_title():
    record = {**_record(), "name_es": "Semillas de girasol 2KEEP"}
    result = audit_repaired_rows([record], [_row(标题="葵花籽")], allowed_tokens={"2KEEP"})
    assert not any(item["code"] == "NUMERIC_DROPPED" for item in result["findings"])


def test_export_audit_does_not_require_confirmed_brand_code_in_no_brand_title():
    record = {**_record(), "name_es": "Limpiador de frenos GS27"}
    result = audit_repaired_rows([record], [_row(标题="刹车清洁剂")], allowed_tokens={"GS27"})
    assert not any(item["code"] in {"MODEL_DROPPED", "PROTECTED_TOKEN_MISSING"} for item in result["findings"])


def test_confirmed_source_token_repair_is_idempotent():
    first = repair_title("产品", "Producto TCX")
    second = repair_title(first, "Producto TCX")
    assert second == first


def test_source_model_suffix_is_restored_without_rewriting_nutrient_names():
    assert repair_source_model_fragments("参数：4", "实用 A4 方格本") == "参数：A4"
    assert repair_source_model_fragments("清洁剂｜27", "刹车清洁剂 GS27") == "清洁剂｜GS27"
    assert repair_source_model_fragments("维生素D片｜10", "维生素 D 10 mcg") == "维生素D片｜10"


def test_source_bound_numeric_and_alias_equivalence_is_deterministic():
    assert _numeric_addition_explained(
        "2 tazas y media de café",
        "2.5杯咖啡",
        {"extra": {"2.5": 1}},
    )
    assert _numeric_addition_explained(
        "¡Vuelta a los 90!",
        "20世纪90年代",
        {"extra": {"20": 1}},
    )
    assert _numeric_addition_explained(
        "12 200 rpm",
        "12200转/分钟",
        {"extra": {"12200": 1}},
    )
    assert not _numeric_addition_explained(
        "Sin cantidad declarada",
        "含50件",
        {"extra": {"50": 1}},
        "Sin cantidad declarada 50 ml",
    )
    assert _source_bound_alias("TV", "Con tus personajes favoritos de TV", "带热门卡通人物图案")
    assert _source_bound_alias("AA", "Pilas alcalinas Varta AA", "碱性电池")
    assert _source_bound_alias("UV", "Protección UV Hair Theory", "防晒护发喷雾")


def test_chinese_remarks_translate_known_official_spanish_tags():
    assert _translate_official_tags("Una opción más sostenible | Nuevo | -21%") == "更可持续的选择｜新品｜-21%"
    assert "Una opción más sostenible" not in _zh_remarks({"raw_tags": "Una opción más sostenible"}, [])


def test_title_drops_brand_number_fragments_but_keeps_model_numbers():
    assert repair_title("跳绳｜31", "Comba Lab31") == "跳绳"
    assert repair_title("派对眼镜｜2", "Gafas Cool2Party") == "派对眼镜"
    assert repair_title("相框｜A4", "Marco A4") == "相框｜A4"


def test_description_and_details_preserve_confirmed_source_brand_tokens():
    from action_tracker.exporting.repair_rules import repair_content_with_context, repair_details_with_context

    assert "Nutini" in repair_content_with_context("Nutini巧克力饼干", "Galletas Nutini", excluded_tokens={"Nutini"})
    assert "Nutini" in repair_details_with_context("品牌：Nutini；商品编号：1", "Marca: Nutini; Número del artículo: 1", excluded_tokens={"Nutini"})


def test_description_removes_adapter_parameter_and_repeated_size_tails():
    assert _repair_export_description("三层结构；参数：3", "Alfombrilla para cortar") == "三层结构"
    assert _repair_export_description(
        "坚固实用的铁丝网垃圾桶。；尺寸：30×35厘米", "Papelera"
    ) == "坚固实用的铁丝网垃圾桶"
    # An unrepeated size remains part of the prose.
    assert _repair_export_description("产品尺寸：30×35厘米", "Papelera") == "产品尺寸：30×35厘米"


def test_details_strip_ordinary_uppercase_fragment_after_article_number():
    assert _repair_export_details(
        "计算器类型：计算器；商品编号：2527246；CALCULADORA",
        "Tipo: Calculadora; Número del artículo: 2527246; CALCULADORA",
    ) == "计算器类型：计算器；商品编号：2527246"
    assert _repair_export_details(
        "商品编号：2542277；MANOS", "Tipo: MANOS; Número del artículo: 2542277"
    ) == "商品编号：2542277"
    # A real source fact after the article marker must remain visible.
    assert _repair_export_details(
        "商品编号：1001；USB-C", "Tipo: Cable; Número del artículo: 1001; USB-C"
    ) == "商品编号：1001；USB-C"


def test_spec_rebuild_discards_cross_field_appended_segments():
    assert repair_spec(
        "50×60cm｜多种颜色｜蓝色", "50x60 cm | varios colores"
    ) == "50×60cm｜多种颜色"
    assert repair_spec("3×50g", "3x50 gramos") == "3×50g"


def test_blocking_unresolved_finding_closes_release_gate():
    report = ExportRepairReport(run_id="run-stale")
    report.add_unresolved(
        sku="1001", field="name_zh", source_field="name_es",
        code="STALE_OVERRIDE", source="Producto A4", target="商品 A4",
    )
    report.set_audit({"release_ready": True, "p0_findings": 0})
    assert report.audit["unresolved_blocking_count"] == 1
    assert report.audit["release_ready"] is False


def test_finalize_blocker_recloses_a_previously_open_release_gate():
    report = ExportRepairReport(run_id="finalize-order")
    report.set_audit({"release_ready": True, "p0_findings": 0})
    report.finalize([_record()], [_row(标题="")])
    assert report.audit["release_ready"] is False
    assert report.audit["unresolved_blocking_count"] == 1


def test_override_requires_exact_source_hash(tmp_path):
    record = _record()
    path = tmp_path / "export_repair_overrides.csv"
    path.write_text(
        ",".join(HEADERS) + "\n"
        + ",".join([
            "1001", "name_zh", field_source_hash(record, "name_zh"), "人工品名", "review", "1.0",
            "tester", "2026-10-05", "APPROVED",
        ]) + "\n",
        encoding="utf-8",
    )
    loaded = load_overrides(path)
    assert loaded[("1001", "name_zh")]["replacement"] == "人工品名"


def test_override_field_hash_survives_unrelated_source_change_and_records_approval(tmp_path):
    record = _record()
    path = tmp_path / "export_repair_overrides.csv"
    path.write_text(
        ",".join(HEADERS) + "\n" + ",".join([
            "1001", "name_zh", field_source_hash(record, "name_zh"), "人工品名", "reviewed source", "2.0",
            "human:tester", "2026-10-06", "APPROVED",
        ]) + "\n", encoding="utf-8",
    )
    changed = {**record, "spec_es": "500 gramos"}
    report = ExportRepairReport(run_id="field-hash")
    value = ExportRepairEngine(report=report, overrides_path=path).apply_override(
        sku="1001", field="name_zh", value="旧品名", record=changed, source_field="name_es",
    )
    assert value == "人工品名"
    event = report.events[-1].as_dict()
    assert event["approved_by"] == "human:tester"
    assert event["reason"] == "reviewed source"


@pytest.mark.parametrize("separator", ["\n", "\r\n", ";", "；", " | ", "｜"])
def test_delivery_cleanup_handles_real_history_annotation_separators(separator):
    from action_tracker.exporting.repair_rules import clean_review_notes
    original = separator.join(["颜色：米色", "来源异常：官网字段Incluye oído=Sí", "材质：陶瓷", "商品编号：3217835"])
    expected = separator.join(["颜色：米色", "材质：陶瓷", "商品编号：3217835"])
    assert clean_review_notes(original) == expected
    assert clean_review_notes(expected) == expected


@pytest.mark.parametrize("sku,note", [
    ("3217518", "来源异常：官网字段Sustancia=Válido, Loción（Loción译为乳液，Válido保留官网异常原词）"),
    ("3217835", "来源异常：官网字段Incluye oído=Sí"),
    ("3218940", "来源异常：官网字段Incluye oído=Sí"),
    ("3219068", "来源异常：官网字段Incluye oído=No"),
])
def test_four_real_sku_note_regressions_keep_facts(sku, note):
    from action_tracker.exporting.repair_rules import clean_review_notes
    original = "数量：4件\n" + note + "\n商品编号：" + sku
    assert clean_review_notes(original) == "数量：4件\n商品编号：" + sku


def test_cleanup_keeps_business_tags_and_ordinary_uses_of_review_words():
    from action_tracker.exporting.repair_rules import clean_review_notes
    assert clean_review_notes("在售；新品\n【复核·详情·来源待核】未知；促销", remarks=True) == "在售；新品；促销"
    note = "【原有来源待核：产品详情；【来源异常】官网物态字段无有效物态值；不得据此推断固态、液态或凝胶。"
    assert clean_review_notes(note, remarks=True) is None
    assert clean_review_notes("适用于复核账目；不得用于微波炉") == "适用于复核账目；不得用于微波炉"
    assert clean_review_notes("Nuevo;Revisar texto de origen;Promoción", remarks=True, language="es") == "Nuevo;Promoción"
    assert clean_review_notes("Evita la condensación de cristales", language="es") == "Evita la condensación de cristales"


def test_cleanup_is_explicit_and_cannot_turn_empty_translation_into_qa_pass(tmp_path, monkeypatch):
    from action_tracker.exporting import service
    from test_exporting import _record as fact_record
    record = fact_record(); source = service.ExportSource("2026-08-24", "run", "SQLITE_CURRENT", (record,), None, source_commit_id="head")
    row = service.build_es_rows([record])[0]
    row.update({"标题": "待复核：来源字段存在异常，详见备注", "分类1": "家务清洁", "分类2": "清洁用品", "规格": "2件装", "描述": "清洁用品", "产品详情": "材质：塑料", "备注": "在售；来源冲突待核"})
    original = deepcopy(row)
    monkeypatch.setattr(service, "build_zh_rows_from_localized_source", lambda *a, **k: ([dict(row)], {}))
    monkeypatch.setattr(service, "audit_repaired_rows", lambda *a, **k: {"release_ready": True, "p0_findings": 0})
    cfg = {"project_root": tmp_path, "paths": {}}
    assert service.build_final_zh_projection(cfg, source).rows[0]["标题"] == row["标题"]
    cfg["export_delivery"] = {"remove_review_notes": True}
    projection = service.build_final_zh_projection(cfg, source)
    assert projection.rows[0]["标题"] is None
    assert projection.repair_audit["release_ready"] is False
    assert projection.delivery_note_cleanup["changed_cells"] == 2
    assert any(e.rule == "DELIVERY_REVIEW_NOTE_CLEANUP" and e.before == original["标题"] for e in projection.repair_report.events)
    assert row == original
    with pytest.raises(service.ExportValidationError, match="EXPORT_REPAIR_GATE_FAILED"):
        service.build_final_zh_projection(cfg, source, release_mode=service.PRODUCTION_RELEASE)
    with pytest.raises(service.ExportValidationError, match="ANNOTATED_DELIVERY_REQUIRES_REVIEW_NOTES"):
        service.build_final_zh_projection(cfg, source, release_mode=service.ANNOTATED_RELEASE)
    cfg["export_delivery"]["remove_review_notes"] = "false"
    with pytest.raises(service.ExportValidationError, match="MUST_BE_BOOLEAN"):
        service.build_final_zh_projection(cfg, source)


def _approved_file(path, record, **updates):
    row = dict(sku=record["sku"], field="name_zh", source_hash=field_source_hash(record, "name_zh"), replacement="已核对中文", reason="own Spanish field checked", rule_version="1.0", approved_by="human:reviewer", approved_at="2026-10-27", status="APPROVED")
    row.update(updates)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADERS); w.writeheader(); w.writerow(row)


@pytest.mark.parametrize("field", ["reason", "approved_at", "approved_by", "replacement", "rule_version"])
def test_approved_override_requires_complete_evidence(tmp_path, field):
    from action_tracker.exporting.repair_overrides import ExportRepairOverrideError
    path = tmp_path / "overrides.csv"; _approved_file(path, _record(), **{field: ""})
    with pytest.raises(ExportRepairOverrideError, match="APPROVAL_REQUIRED"):
        load_overrides(path)


def test_approved_correction_rejects_missing_or_changed_own_source(tmp_path):
    record = _record(); path = tmp_path / "overrides.csv"; _approved_file(path, record)
    report = ExportRepairReport(); engine = ExportRepairEngine(report=report, overrides_path=path)
    assert engine.apply_override(sku="1001", field="name_zh", value="旧中文", record={**record, "name_es": ""}, source_field="name_es") == "旧中文"
    assert report.unresolved[-1].code == "OVERRIDE_SOURCE_MISSING"
    assert engine.apply_override(sku="1001", field="name_zh", value="旧中文", record={**record, "name_es": "Otro producto"}, source_field="name_es") == "旧中文"
    assert report.unresolved[-1].code == "STALE_OVERRIDE"
    assert engine.apply_override(sku="1001", field="name_zh", value="旧中文", record={**record, "spec_es": "500 gramos"}, source_field="name_es") == "已核对中文"
    assert report.events[-1].approval_source == str(path)


@pytest.mark.parametrize("sqlite", [False, True])
@pytest.mark.parametrize("field,column,replacement", [("cat1_zh", "分类1", "家居布置"), ("cat2_zh", "分类2", "家用清洁用品"), ("unit_price_zh", "单价", "1,25欧元/件")])
def test_categories_and_unit_price_use_the_existing_approved_override_path(tmp_path, sqlite, field, column, replacement):
    from test_exporting import _record as fact_record, _write_dictionary
    from action_tracker.exporting.dictionary_join import load_dictionary_context, build_zh_rows, build_zh_rows_from_localized_source
    record = fact_record(); root = tmp_path / "dict"; _write_dictionary(root, record)
    context = load_dictionary_context({"paths": {"dictionary_baseline": root, "dictionary": tmp_path / "runtime_dict"}})
    path = tmp_path / "override.csv"
    _approved_file(path, record, field=field, source_hash=field_source_hash(record, field), replacement=replacement)
    report = ExportRepairReport()
    if sqlite:
        rows, _ = build_zh_rows_from_localized_source([record], category_context=context, repair_report=report, repair_overrides_path=path)
    else:
        rows, _ = build_zh_rows([record], context, repair_report=report, repair_overrides_path=path)
    assert rows[0][column] == replacement
    assert any(e.field == field and e.rule == "APPROVED_EXPORT_OVERRIDE" for e in report.events)


@pytest.mark.parametrize("kind", ["MASTER_CURRENT", "FORMAL_SNAPSHOT"])
def test_source_bound_primary_chinese_wins_over_old_dictionary_on_file_paths(tmp_path, monkeypatch, kind):
    from test_exporting import _cfg, _record as fact_record, _write_dictionary
    from action_tracker.exporting import service
    from action_tracker.database.repository import ProductionRepository
    record = fact_record(); record["name_es"] = "Paño de limpieza"
    reference = {**record, "name_zh": "已核对清洁布", "zh_freshness_status": "CURRENT"}
    cfg = _cfg(tmp_path); cfg["storage"] = {"mode": "SQLITE_PRIMARY"}
    _write_dictionary(cfg["paths"]["dictionary_baseline"], record)
    monkeypatch.setattr(ProductionRepository, "__init__", lambda self, path: None)
    monkeypatch.setattr(ProductionRepository, "load_current_export_records", lambda *a, **k: [reference])
    source = service.ExportSource("2026-08-24", "run", kind, (record,), None)
    projection = service.build_final_zh_projection(cfg, source)
    assert projection.rows[0]["标题"] == "已核对清洁布"
    changed = {**record, "name_es": "Otro producto"}
    reused, audit = service._reuse_source_bound_localizations(cfg, [changed])
    assert "name_zh" not in reused[0].get("_source_bound_localization_fields", ())
    assert audit["source_changed_counts"]["name_zh"] == 1
    reference["zh_field_provenance"] = {"name": {"source_hash": "0" * 64, "freshness_status": "CURRENT", "value": "旧版本中文"}}
    reused, audit = service._reuse_source_bound_localizations(cfg, [record])
    assert "name_zh" not in reused[0].get("_source_bound_localization_fields", ())
    assert audit["source_changed_counts"]["name_zh"] == 1


def test_review_blocks_keep_business_notes_and_normal_uses_of_review():
    from action_tracker.exporting.repair_rules import clean_review_notes
    text = "在售状态：在售；新品\n待核字段：标题；正式品牌身份尚未确认；西语原文：Producto\n包装文字引用\n【品名待核】需核对\n促销；注意：远离火源"
    assert clean_review_notes(text, remarks=True) == "在售状态：在售；新品\n促销；注意：远离火源"
    assert clean_review_notes("适用于复核账目", remarks=True) == "适用于复核账目"
    assert clean_review_notes("颜色：米色 | 来源异常：官网字段Incluye oído=Sí | 材质：陶瓷") == "颜色：米色 | 材质：陶瓷"


def test_real_3217754_details_after_leading_note_are_not_emptied():
    from action_tracker.exporting.repair_rules import clean_review_notes
    # Own Spanish: Tipo de desatascador de tuberías: Abridor de tuberías -
    # uso general; Tipo de dispensador: Botella; Número del artículo: 3217754.
    facts = "管道疏通产品类型：通用管道疏通产品；分配器类型：瓶；商品编号：3217754"
    assert clean_review_notes("来源异常：官网字段Sustancia=Válido；" + facts) == facts


def test_reviewed_workbook_handoff_never_auto_approves_and_requires_same_source(tmp_path):
    from action_tracker.exporting.repair_overrides import prepare_reviewed_candidates, ExportRepairOverrideError
    record = _record(); current = _row(); reviewed = {**current, "标题": "清洁用品 A4", "单价": "1欧元/件"}
    result = prepare_reviewed_candidates([record], [current], [reviewed], [record], evidence_ref="fixture.xlsx SHA256 verified")
    assert len(result["candidates"]) == 1 and not result["blocked"]
    candidate = result["candidates"][0]
    assert candidate["status"] == "PENDING" and not candidate["approved_by"] and not candidate["approved_at"]
    assert candidate["source_hash"] == field_source_hash(record, "name_zh")
    p = tmp_path / "candidates.csv"
    with p.open("w", encoding="utf8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS); writer.writeheader(); writer.writerows(result["candidates"])
    assert load_overrides(p) == {}
    changed = {**record, "name_es": "Otro producto"}
    r = prepare_reviewed_candidates([changed], [current], [reviewed], [record], evidence_ref="fixture")
    assert not r["candidates"] and r["blocked"][0]["code"] == "SOURCE_CHANGED"
    with pytest.raises(ExportRepairOverrideError, match="SKU_SET_MISMATCH"):
        prepare_reviewed_candidates([record], [current], [], [record], evidence_ref="fixture")


@pytest.mark.parametrize("selected", [False, True])
def test_catalog_template1_publish_same_approved_clean_projection(tmp_path, monkeypatch, selected):
    import openpyxl
    from test_exporting import _cfg, _record as fact_record, _write_dictionary
    from test_template1 import _write_seed
    from action_tracker.exporting import service
    from action_tracker.exporting.template1_service import export_template1
    record = fact_record(last_seen="2026-08-26")
    record.update(name_es="Paño de limpieza", name_zh="清洁布", details_es="Material: Plástico",
                  details_zh="材质：塑料\n来源异常：官网字段Incluye oído=Sí", zh_freshness_status="CURRENT", zh_review_status="VERIFIED")
    cfg = _cfg(tmp_path)
    cfg["export_profiles_path"] = cfg["project_root"] / "config" / "export_profiles.yaml"
    cfg["project_root"] = tmp_path
    cfg["export_delivery"] = {"remove_review_notes": True}
    _write_dictionary(cfg["paths"]["dictionary_baseline"], record)
    approved = tmp_path / "approved.csv"; _approved_file(approved, record, replacement="家用清洁布")
    cfg["export_repair_overrides_path"] = approved
    selection_id = None
    source_commit = "head"
    source_records = (record,)
    if selected:
        from action_tracker.database.production import CommitBundle, ProductionWriter
        from action_tracker.extraction.selections import SelectionService
        db_path = tmp_path / "selection.sqlite3"
        cfg["storage"] = {"db_path": db_path}
        source_commit = ProductionWriter(db_path, role="PRIMARY").commit(CommitBundle(
            run_id="selection-seed", observation_date="2026-08-26", qa_state="PASS",
            current_products=({"sku": record["sku"], "canonical_id": record["canonical_id"],
                               "name_es": record["name_es"], "status": "CURRENT"},)))
        selection_id = SelectionService(db_path).create("Chosen product", {"skus": [record["sku"]]})["selection_id"]
        # The unselected product is deliberately not covered by the approved
        # repair. Both consumers must filter membership before projecting.
        source_records = (record, fact_record("1002", last_seen="2026-08-26"))
    source = service.ExportSource("2026-08-26", "fixture", "SQLITE_CURRENT", source_records, None, source_commit_id=source_commit)
    monkeypatch.setattr(service, "resolve_formal_source", lambda *a, **k: source)
    # Template1 binds its imported resolver separately; all downstream builders
    # and both writers remain real, sharing one source-bound approved CSV.
    monkeypatch.setattr("action_tracker.exporting.template1_service.resolve_formal_source", lambda *a, **k: source)
    seed = tmp_path / "seed.xlsx"; _write_seed(seed)
    config = tmp_path / "history.yaml"
    config.write_text("seed:\n  path: '" + seed.as_posix() + "'\n  sheet: ACTION商品上下架明细\n  sku_header: 编号\n  presence_capability: true\n  absence_capability: true\n  observation_complete: true\n  evidence_level: A\nsources: []\n", "utf8")
    cfg["history_sources_path"] = config
    a = service.export_catalog(cfg, language="zh", export_date="2026-08-26", no_images=True, selection_id=selection_id)
    b = export_template1(cfg, export_date="2026-08-26", selection_id=selection_id)
    def read(path, sheet=None):
        w = openpyxl.load_workbook(path, read_only=True, data_only=True)
        rows = list((w[sheet] if sheet else w.active).values); w.close(); return rows
    published = read(a["output"])
    assert published == read(b["output"], "今日中文清单")
    row = dict(zip(published[0], published[1]))
    assert len(published[0]) == 14 and len(published) == 2
    assert row["编号"] == record["sku"]
    assert row["标题"] == "家用清洁布" and row["产品详情"] == "材质：塑料"
    assert row["折后价"] == record["current_price"] and row["商品链接"] == record["product_url"]
    manifest = json.loads(Path(a["manifest"]).read_text("utf8"))
    assert manifest["export_repair"]["audited_zh_rows_hash"] == manifest["export_repair"]["published_zh_rows_hash"]
    assert manifest["delivery_note_cleanup"]["changed_cells"] > 0
    report = json.loads(Path(a["repair_report"]).read_text("utf8"))
    assert any(e["rule"] == "APPROVED_EXPORT_OVERRIDE" and e["approval_source"] == str(approved) for e in report["repairs"])
    if selected:
        from action_tracker.delivery.artifacts import ArtifactService
        artifacts = ArtifactService(db_path).list(selection_id)
        assert len(artifacts) == 2
        assert all(item["source_commit_id"] == source_commit and item["row_count"] == 1 for item in artifacts)
    again = service.export_catalog(cfg, language="zh", export_date="2026-08-26", no_images=True, selection_id=selection_id)
    assert published == read(again["output"])
