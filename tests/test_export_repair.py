from action_tracker.exporting.repair import ExportRepairEngine
from action_tracker.exporting.repair_overrides import HEADERS, load_overrides, source_hash
from action_tracker.exporting.repair_report import (
    ExportRepairReport,
    _numeric_addition_explained,
    _source_bound_alias,
    audit_repaired_rows,
)
from action_tracker.exporting.repair_rules import repair_spec, repair_title
from action_tracker.exporting.repair_tokens import repair_source_model_fragments


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


def test_blocking_unresolved_finding_closes_release_gate():
    report = ExportRepairReport(run_id="run-stale")
    report.add_unresolved(
        sku="1001", field="name_zh", source_field="name_es",
        code="STALE_OVERRIDE", source="Producto A4", target="商品 A4",
    )
    report.set_audit({"release_ready": True, "p0_findings": 0})
    assert report.audit["unresolved_blocking_count"] == 1
    assert report.audit["release_ready"] is False


def test_override_requires_exact_source_hash(tmp_path):
    record = _record()
    path = tmp_path / "export_repair_overrides.csv"
    path.write_text(
        ",".join(HEADERS) + "\n"
        + ",".join([
            "1001", "name_zh", source_hash(record), "人工品名", "review", "1.0",
            "tester", "2026-10-05", "APPROVED",
        ]) + "\n",
        encoding="utf-8",
    )
    loaded = load_overrides(path)
    assert loaded[("1001", "name_zh")]["replacement"] == "人工品名"
