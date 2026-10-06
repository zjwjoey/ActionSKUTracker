from action_tracker.workflow_v2.export_audit import audit_zh


def test_zh_audit_blocks_unapproved(source_row):
    assert audit_zh([source_row])["status"] == "FAIL"


def test_zh_audit_blocks_residue_and_unit_change(source_row):
    row = dict(source_row)
    row.update({
        "translation_status": "APPROVED",
        "name_zh": "Mesa",
        "cat1_zh": "家居",
        "cat2_zh": "家具",
        "spec_zh": "10 kg",
        "desc_zh": "红色桌子",
        "details_zh": "编号100",
    })
    result = audit_zh([row])
    codes = {item["code"] for item in result["issues"]}
    assert "SPANISH_RESIDUAL" in codes
    assert "UNIT_DROPPED" in codes


def test_zh_structural_audit_defers_field_facts_to_final_projection(source_row):
    row = dict(source_row)
    row.update({
        "translation_status": "APPROVED",
        "name_zh": "Mesa",
        "cat1_zh": "家居",
        "cat2_zh": "家具",
        "spec_zh": "10 kg",
        "desc_zh": "红色桌子",
        "details_zh": "编号100",
    })
    assert audit_zh([row], include_field_fact_audit=False)["status"] == "PASS"
