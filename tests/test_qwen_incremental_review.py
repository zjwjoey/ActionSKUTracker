from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "review_qwen_incremental_with_deepseek.py"
SPEC = importlib.util.spec_from_file_location("review_qwen_incremental", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def _six(**overrides: str) -> dict[str, str]:
    result = {field: "" for field in MODULE.FIELDS}
    result.update(overrides)
    return result


def test_duplicate_raw_numeric_facts_are_safe_to_collapse_but_values_cannot_change():
    source = _six(details="Potencia: 20; Potencia: 20 vatio")
    target = _six(details="功率：20瓦")
    remaining, exceptions = MODULE._guard_with_safe_normalizations(source, target, ())
    assert remaining == []
    assert exceptions == []

    conflict_source = _six(details="Lumen: 3000")
    conflict_target = _six(details="流明：300")
    remaining, exceptions = MODULE._guard_with_safe_normalizations(conflict_source, conflict_target, ())
    assert "details:NUMERIC_DROPPED" in remaining
    assert "details:NUMERIC_HALLUCINATED" in remaining
    assert exceptions == []


def test_source_proper_name_is_not_treated_as_english_residual():
    source = _six(description="Para elegir entre Spidey, Hot Wheels y Winnie the Pooh")
    target = _six(description="可选Spidey、Hot Wheels和Winnie the Pooh主题")
    assert MODULE._source_proper_phrases(source, target) == ("Hot Wheels", "Winnie the Pooh")
    remaining, _ = MODULE._guard_with_safe_normalizations(source, target, ())
    assert remaining == []


def test_source_proper_name_with_spanish_connector_is_not_treated_as_residual():
    source = _six(description="Para elegir entre Angel en Stitch, entre otros")
    target = _six(description="可选Angel en Stitch等款式")
    assert MODULE._source_proper_phrases(source, target) == ("Angel en Stitch",)
    remaining, _ = MODULE._guard_with_safe_normalizations(source, target, ())
    assert remaining == []


def test_confirmed_brand_is_removed_only_when_exactly_present_in_official_name():
    context = SimpleNamespace(
        manual_by_sku={},
        product_by_sku={"1": {"brand_id": "Disney"}},
        brand_by_id={"Disney": {
            "brand_id": "Disney", "canonical_name": "Disney", "aliases_es": "Disney",
            "review_status": "HUMAN_REVIEWED", "confidence": "REFERENCE",
        }},
    )
    assert MODULE._normalize_confirmed_brand_name(context, "1", "Disney牌文具盒", "Estuche Disney") == (
        "文具盒", True,
    )
    assert MODULE._normalize_confirmed_brand_name(context, "1", "拼图", "Rompecabezas") == (
        "拼图", False,
    )

    context.manual_by_sku["1"] = {"name_zh_standard": "人工文具盒"}
    assert MODULE._normalize_confirmed_brand_name(context, "1", "Disney牌文具盒", "Estuche Disney") == (
        "人工文具盒", True,
    )
    assert MODULE._has_unresolved_chinese_brand_marker(("Disney",), "迪士尼牌文具盒") is True
    assert MODULE._has_unresolved_chinese_brand_marker(("Disney",), "文具盒") is False
    assert MODULE._has_unresolved_chinese_brand_marker((), "迪士尼牌文具盒") is False


def test_confirmed_brands_are_found_in_new_sku_source_title_and_cobrands_remain_ambiguous():
    context = SimpleNamespace(
        manual_by_sku={}, product_by_sku={},
        brand_by_id={
            "Max & More": {
                "brand_id": "Max & More", "canonical_name": "Max & More", "aliases_es": "",
                "review_status": "HUMAN_REVIEWED", "confidence": "REFERENCE",
            },
            "Milka": {
                "brand_id": "Milka", "canonical_name": "Milka", "aliases_es": "",
                "review_status": "HUMAN_REVIEWED", "confidence": "REFERENCE",
            },
            "Oreo": {
                "brand_id": "Oreo", "canonical_name": "Oreo", "aliases_es": "",
                "review_status": "HUMAN_REVIEWED", "confidence": "REFERENCE",
            },
        },
    )
    assert MODULE._confirmed_source_brands(context, "new", "Iluminador Max & More") == ("Max & More",)
    assert MODULE._normalize_confirmed_brand_name(context, "new", "Max & More牌高光粉", "Iluminador Max & More") == (
        "高光粉", True,
    )
    assert MODULE._confirmed_source_brands(context, "new", "Bombones Milka Oreo") == ("Milka", "Oreo")
    assert MODULE._normalize_confirmed_brand_name(context, "new", "Milka Oreo 巧克力", "Bombones Milka Oreo") == (
        "Milka Oreo 巧克力", False,
    )


def test_brand_removal_preserves_source_supported_model_and_function_facts():
    context = SimpleNamespace(
        manual_by_sku={}, product_by_sku={},
        brand_by_id={"Disney": {
            "brand_id": "Disney", "canonical_name": "Disney", "aliases_es": "",
            "review_status": "HUMAN_REVIEWED", "confidence": "REFERENCE",
        }},
    )
    source_name = "Set Disney 7 en 1 modelo X100"
    candidate_name = "Disney牌7合1工具 X100"
    normalized, changed = MODULE._normalize_confirmed_brand_name(
        context, "new", candidate_name, source_name,
    )
    assert changed is True
    assert normalized == "7合1工具 X100"
    remaining, _ = MODULE._guard_with_safe_normalizations(
        _six(name=source_name), _six(name=normalized), ("Disney",),
    )
    assert remaining == []


def test_finalize_persists_empty_resolution_document_when_none_was_supplied(tmp_path, monkeypatch):
    context = SimpleNamespace(manual_by_sku={}, product_by_sku={}, brand_by_id={})
    monkeypatch.setattr(MODULE, "load_settings", lambda _path: object())
    monkeypatch.setattr(MODULE, "load_dictionary_context", lambda _settings: context)
    source = _six(
        name="Producto", cat1="Hogar", cat2="Sala", spec="1 unidad",
        description="Sin información adicional", details="Número del artículo: 1",
    )
    target = _six(
        name="商品", cat1="家居布置", cat2="客厅", spec="1件",
        description="无额外信息", details="商品编号：1",
    )
    source_hash = MODULE._source_hash(source)
    candidate = {
        "messages": [
            {"role": "system", "content": ""},
            {"role": "user", "content": json.dumps(source, ensure_ascii=False)},
            {"role": "assistant", "content": json.dumps(target, ensure_ascii=False)},
        ],
        "metadata": {"sku": "1", "source_hash": source_hash, "label_tier": "HUMAN_REVIEWED"},
    }
    first = {
        "sku": "1", "source": source, "original_target": target,
        "verdict": "PASS", "corrected": {}, "reason": "",
        "source_hash": source_hash, "label_tier": "HUMAN_REVIEWED",
    }
    source_path = tmp_path / "candidate.jsonl"
    first_path = tmp_path / "first.jsonl"
    second_path = tmp_path / "second.jsonl"
    source_path.write_text(json.dumps(candidate, ensure_ascii=False) + "\n", encoding="utf-8")
    first_path.write_text(json.dumps(first, ensure_ascii=False) + "\n", encoding="utf-8")
    second_path.write_text("", encoding="utf-8")
    resolution_path = tmp_path / "resolutions.json"

    manifest = MODULE._finalize(
        [candidate], [first], [], tmp_path / "approved.jsonl", tmp_path / "audit.csv",
        tmp_path / "manifest.json", source_path, first_path, second_path, resolution_path,
    )

    assert json.loads(resolution_path.read_text(encoding="utf-8")) == {"decisions": []}
    assert manifest["status"] == "REVIEW_COMPLETE"
    assert manifest["approved"] == 1
    assert manifest["candidate_count"] == 1
    assert manifest["entire_candidate_set_training_eligible"] is True


def test_artifact_prefix_keeps_legacy_names_and_validates_labels():
    assert MODULE._artifact_prefix("") == "qwen_incremental_"
    assert MODULE._artifact_prefix("fresh_v2") == "qwen_incremental_fresh_v2_"
    try:
        MODULE._artifact_prefix("Fresh V2")
    except ValueError as exc:
        assert str(exc) == "INVALID_ARTIFACT_LABEL"
    else:
        raise AssertionError("expected invalid label to be rejected")


def test_incomplete_model_correction_is_rejected_without_aborting_batch(tmp_path, monkeypatch):
    source = _six(name="Producto", cat1="Hogar", cat2="Sala", spec="1 unidad", description="Texto", details="Número: 1")
    target = _six(name="商品", cat1="家居布置", cat2="客厅", spec="1件", description="文本", details="编号：1")
    candidate = {
        "messages": [
            {"role": "system", "content": ""},
            {"role": "user", "content": json.dumps(source, ensure_ascii=False)},
            {"role": "assistant", "content": json.dumps(target, ensure_ascii=False)},
        ],
        "metadata": {"sku": "1", "source_hash": MODULE._source_hash(source), "label_tier": "REMEDIATION_CANDIDATE_NOT_GOLD"},
    }
    monkeypatch.setattr(MODULE, "_checked_call", lambda *_args: [{"sku": "1", "verdict": "REVISE", "corrected": {"name": "仅有品名"}, "reason": "partial"}])

    result = MODULE._first_pass([candidate], tmp_path / "first.jsonl", "unused", 1, 0)

    assert result[0]["verdict"] == "REJECT"
    assert result[0]["reason"] == "FIRST_PASS_CORRECTION_INCOMPLETE"
