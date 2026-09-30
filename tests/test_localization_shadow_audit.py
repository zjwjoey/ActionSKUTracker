from pathlib import Path

from action_tracker.localization.shadow_audit import run_shadow_audit


def test_shadow_audit_is_read_only_and_finds_missing_translation():
    records = [{
        "sku": "1", "name_es": "Producto", "name_zh": "",
        "cat1_es": "Hogar", "cat1_zh": "家居", "cat2_es": "", "cat2_zh": "",
        "spec_es": "", "spec_zh": "", "desc_es": "Texto", "desc_zh": "文本",
        "details_es": "", "details_zh": "",
    }]
    result = run_shadow_audit(records, detail_rules_path=Path("config/stage5/detail_terminology_rules.json"))
    assert result["status"] == "REVIEW_REQUIRED"
    assert result["master_writes"] == 0
    assert result["production_apply"] is False
    assert any(item["code"] == "TRANSLATION_MISSING_REVIEW" for item in result["findings"])


def test_shadow_audit_blocks_nonempty_target_for_empty_source():
    result = run_shadow_audit([{"sku": "1", "name_es": "", "name_zh": "不应存在"}])
    assert result["status"] == "BLOCKED"
    assert result["blocking_count"] == 1
