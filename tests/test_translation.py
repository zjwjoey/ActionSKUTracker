"""Formal adapter: preserve Chinese and keep untranslated facts pending."""
from action_tracker.translation.service import apply_zh_formal


def test_translation_without_approval_is_pending():
    rec = {"name_es": "X", "name_zh": "\u4e2d\u6587"}
    assert apply_zh_formal(rec)["translation_status"] == "PENDING"


def test_missing_chinese_is_not_replaced_by_spanish():
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
    original = dict(rec)
    out = apply_zh_formal(rec)
    assert all(out[field] == "" for field in ("name_zh", "spec_zh", "desc_zh", "details_zh"))
    assert out["translation_status"] == "PENDING"
    assert out["display_fallback"] == "ES"
    assert out["translation_missing_fields"] == ("name_zh", "spec_zh", "desc_zh", "details_zh")
    assert rec == original


def test_zh_present_ok():
    rec = {"name_es": "X", "name_zh": "X中", "translation_status": "OK"}
    out = apply_zh_formal(rec)
    assert out["name_zh"] == "X中"
    assert out["translation_status"] == "OK"
