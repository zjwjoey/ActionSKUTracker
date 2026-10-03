from action_tracker.translation.detail_rule_repair import build_detail_rule_repair_manifest
from action_tracker.localization.repair_service import build_preview


def test_detail_rule_repair_manifest_is_pair_scoped_and_source_bound():
    records = [{
        "sku": "1001",
        "name_es": "Producto",
        "cat1_es": "Hogar",
        "cat2_es": "Limpieza",
        "details_es": "Tipo de batería: alcalina; Cantidad: 2",
        "details_zh": "电池类型：旧译法；数量：2",
    }]
    result = build_detail_rule_repair_manifest(records, [{
        "candidate_id": "detail-rule-1", "section": "value_translations",
        "source_key": "tipo de bateria", "source_value": "alcalina",
        "target_value": "碱性电池", "approved_by": "owner",
        "approved_at": "2026-10-01T00:00:00+00:00",
        "context_scope": ['{"cat1_es":"hogar","cat2_es":"limpieza","name_es":"producto"}'],
    }], policy_manifest_hash="policy")
    assert result["matched_occurrences"] == 1
    row = result["rows"][0]
    assert row["field"] == "details"
    assert row["operation"] == "REPLACE_DETAILS_PAIR"
    assert row["detail_pair_index"] == 0
    assert row["target_value"] == "碱性电池"
    assert result["master_writes"] == 0
    preview = build_preview(records, result["rows"], expected_policy_hash="policy")
    assert preview[0]["status"] == "WOULD_UPDATE"
