from pathlib import Path

from PIL import Image

from action_tracker.exporting.dictionary_join import build_zh_rows_from_localized_source
from action_tracker.exporting.excel_writer import write_catalog_xlsx
from action_tracker.products.details_parser import conflicting_keys, parse_details, render_details
from action_tracker.services.hashing import localization_field_source_hash, localization_field_source_hashes


def test_source_empty_clears_stale_chinese_description_and_details():
    rows, fallback_counts = build_zh_rows_from_localized_source([{
        "sku": "1001", "name_es": "Producto", "cat1_es": "Hogar", "cat2_es": "Limpieza",
        "spec_es": "", "desc_es": "", "details_es": "",
        "name_zh": "商品", "cat1_zh": "家居布置", "cat2_zh": "家务清洁", "spec_zh": "旧规格",
        "desc_zh": "错误的跨字段描述", "details_zh": "错误的详情",
        "current_price": 1.0, "original_price": None, "unit_price": "1 €/ud.",
        "product_url": "https://www.action.com/es-es/p/1001/", "image_url": "",
        "last_seen": "2026-09-21", "status": "CURRENT",
    }])
    assert rows[0]["描述"] is None
    assert rows[0]["产品详情"] is None
    assert rows[0]["规格"] is None
    assert fallback_counts["中文描述无来源"] == 1
    assert fallback_counts["中文产品详情无来源"] == 1


def test_field_source_hashes_are_independent():
    source = {
        "name_es": "Producto", "cat1_es": "Hogar", "cat2_es": "Limpieza",
        "spec_es": "2 piezas", "desc_es": "Para casa", "details_es": "Material: Plástico",
    }
    hashes = localization_field_source_hashes(source)
    changed = {**source, "details_es": "Material: Metal"}
    changed_hashes = localization_field_source_hashes(changed)
    assert hashes["details"] != changed_hashes["details"]
    for field in ("name", "cat1", "cat2", "spec", "description"):
        assert hashes[field] == changed_hashes[field]
    assert localization_field_source_hash(source, "details") == hashes["details"]


def test_spec_dimension_normalization_does_not_corrupt_protected_tokens():
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "scripts" / "build_formal_chinese_candidate_20260907.py"
    spec = importlib.util.spec_from_file_location("formal_candidate_regression", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert module.normalize_spec("50x100 cm | XL | USB-C | CR2032 | A4") == (
        "50×100cm｜XL｜USB-C｜CR2032｜A4"
    )


def test_field_contract_does_not_import_details_facts_into_description():
    # The description source owns its facts.  A certification present only in
    # details must not make a description candidate look source-supported.
    from scripts.build_formal_chinese_candidate_20260907 import remove_unsupported

    cleaned, reasons = remove_unsupported("Suave y absorbente", "柔软，Better Cotton认证")
    assert "Better Cotton" not in cleaned
    assert "UNSUPPORTED_SOURCE_FACT" in reasons


def test_details_parser_preserves_order_duplicates_and_conflicts():
    pairs = parse_details("Voltaje: 9 V; Material\tPlástico | Voltaje: 12 V")
    assert [(pair.key_es, pair.value_es) for pair in pairs] == [
        ("Voltaje", "9 V"), ("Material", "Plástico"), ("Voltaje", "12 V"),
    ]
    assert conflicting_keys(pairs) == ("Voltaje",)
    assert render_details(pairs) == "Voltaje: 9 V; Material: Plástico; Voltaje: 12 V"


def test_image_manifest_lists_missing_skus_and_keeps_image_row_height(tmp_path: Path):
    image_root = tmp_path / "images"
    image_root.mkdir()
    Image.new("RGB", (250, 250), "white").save(image_root / "1001.png")
    output = tmp_path / "out.xlsx"
    stats = write_catalog_xlsx(
        output,
        headers=["图片", "编号", "描述", "产品详情"],
        rows=[
            {"图片": None, "编号": "1001", "描述": "短", "产品详情": "短"},
            {"图片": None, "编号": "1002", "描述": "", "产品详情": ""},
        ],
        workbook_format={"sheet_name": "商品全量", "auto_filter": True, "freeze_panes": "A2"},
        image_root=image_root,
        embed_images=True,
    )
    assert stats["embedded_count"] == 1
    assert stats["missing_count"] == 1
    assert stats["missing_skus"] == ["1002"]
    import openpyxl
    workbook = openpyxl.load_workbook(output, data_only=True)
    try:
        assert workbook["商品全量"].row_dimensions[2].height >= 190
    finally:
        workbook.close()


def test_guard_pass_remains_a_candidate_not_a_semantic_keep():
    # Stage 5 deliberately exposes guard acceptance as a candidate state;
    # only a separate reviewer may produce KEEP/CORRECTED.
    import importlib.util
    script = Path(__file__).resolve().parents[1] / "scripts" / "qwen_offline_stage5.py"
    spec = importlib.util.spec_from_file_location("qwen_offline_stage5_regression", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    row = {"messages": [{"role": "user", "content": '{"spec":"2 unidades"}'}],
           "metadata": {"sku": "1001", "source_hash": "hash"}}
    candidate = module.classify_candidate(row, {"spec": "2件"})
    assert candidate["accepted_by_guard"] is True
    assert candidate["status"] == "AUTO_READY_CANDIDATE"
    assert "decision" not in candidate or candidate.get("decision") not in {"KEEP", "CORRECTED"}


def test_model_guard_rejects_nonempty_output_for_empty_source():
    from action_tracker.translation.model_guard import validate_model_output

    result = validate_model_output(
        {"description": "", "details": "Cantidad: 3 piezas"},
        {"description": "数量：3件", "details": "数量：3件"},
        expected_fields=("description", "details"),
    )
    assert not result.accepted
    assert "SOURCE_EMPTY_NONEMPTY" in result.field_reasons["description"]


def test_versioned_skills_contain_field_contract_and_semantic_boundary():
    root = Path(__file__).resolve().parents[1] / ".agents" / "skills"
    qwen = (root / "action-qwen-translation" / "SKILL.md").read_text(encoding="utf-8")
    details = (root / "action-cn-description-details-review" / "SKILL.md").read_text(encoding="utf-8")
    localization = (root / "action-cn-localization-review" / "SKILL.md").read_text(encoding="utf-8")
    assert "PRIMARY SOURCE" in qwen and "CONTEXT FOR DISAMBIGUATION" in qwen
    assert "GUARD PASS IS NOT TRANSLATION PASS" in qwen
    assert "GUARD PASS IS NOT TRANSLATION PASS" in details
    assert "Voltaje" in details and "Contenido" in details and "NO_SOURCE" in details
    assert "text.replace(\"x\", \"×\")" in localization
