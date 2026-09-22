from action_tracker.stage5.source_candidate_v2 import (
    candidate_source_provenance,
    consistency_status,
    family_key,
    source_consistency_flags,
    source_quality_issues,
)


def test_source_candidate_is_read_only_and_detects_sku_mismatch():
    source = {
        "name": "Cesta",
        "cat1": "Hogar",
        "cat2": "Almacenamiento",
        "spec": "2 piezas",
        "description": "Cesta",
        "details": "Número del artículo: 9999",
    }
    assert "DETAIL_SKU_MISMATCH" in source_quality_issues(source, "1001")
    assert family_key(source) == "cesta"


def test_source_conflict_is_review_signal_not_repair():
    source = {
        "name": "Producto",
        "spec": "2 piezas",
        "description": "3 piezas",
        "details": "Número del artículo: 1001",
    }
    flags = source_consistency_flags(source)
    assert "NUMERIC_CONFLICT" in flags
    assert consistency_status(flags) == "SOURCE_CONFLICT_REVIEW"


def test_new_candidate_provenance_is_field_scoped():
    source = {"name": "Producto", "description": "Para casa", "details": "Material: Plástico"}
    provenance = candidate_source_provenance(source, "description")
    assert provenance["hash_scope"] == "field"
    assert provenance["source_field"] == "description"
    assert provenance["source_spanish_value"] == "Para casa"
    changed = {**source, "details": "Material: Metal"}
    assert candidate_source_provenance(changed, "description")["source_hash"] == provenance["source_hash"]
