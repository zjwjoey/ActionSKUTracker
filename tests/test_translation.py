"""规范 §60 测试 17：中文为空 fallback 西语。"""
from action_tracker.translation.service import apply_zh


def test_translation_without_provider_is_marked_not_configured():
    rec = {"name_es": "X", "name_zh": "\u4e2d\u6587"}
    assert apply_zh(rec)["translation_status"] == "NOT_CONFIGURED"


def test_t17_zh_fallback():
    rec = {
        "name_es": "Barra de cola",
        "spec_es": "50 ml",
        "desc_es": "Desc",
        "details_es": "Details",
        "name_zh": "",
        "spec_zh": None,
        "desc_zh": None,
        "details_zh": None,
    }
    out = apply_zh(rec)
    assert out["name_zh"] == "Barra de cola"
    assert out["spec_zh"] == "50 ml"
    assert out["desc_zh"] == "Desc"
    assert out["details_zh"] == "Details"
    assert out["translation_status"] == "FALLBACK_ES"


def test_zh_present_ok():
    rec = {"name_es": "X", "name_zh": "X中", "translation_status": "OK"}
    out = apply_zh(rec)
    assert out["name_zh"] == "X中"
    assert out["translation_status"] == "OK"
