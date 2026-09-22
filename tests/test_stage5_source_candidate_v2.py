from __future__ import annotations

from action_tracker.stage5.source_candidate_v2 import (
    FAMILY_KEY_METHOD,
    SOURCE_HASH_CONTRACT_VERSION,
    consistency_status,
    family_key,
    source_consistency_flags,
    source_hash,
    source_quality_issues,
)
from scripts.select_stage5_source_versions_v2 import historical_sets


def _source(**changes):
    value = {
        "name": "Alicates para bomba de agua Werckmann",
        "cat1": "Bricolaje",
        "cat2": "Herramientas",
        "spec": "245 mm",
        "description": "Con mango antideslizante",
        "details": "Tipo de agarre: Agarre de goma; Número del artículo: 2513777",
    }
    value.update(changes)
    return value


def test_v1_hash_contract_and_family_key_are_deterministic():
    assert SOURCE_HASH_CONTRACT_VERSION == "SOURCE_HASH_V1"
    assert FAMILY_KEY_METHOD == "SPANISH_NAME_HEURISTIC_V1"
    assert source_hash(_source()) == source_hash(dict(reversed(list(_source().items()))))
    assert family_key(_source(name="Alicates para bomba de agua Werckmann 245 mm")) == family_key(_source())


def test_source_quality_rejects_empty_pollution_and_article_mismatch():
    assert "EMPTY_DESCRIPTION" in source_quality_issues(_source(description=""), "2513777")
    assert "POLLUTED_NAME" in source_quality_issues(_source(name="<span>Producto</span>"), "2513777")
    assert "DETAIL_SKU_MISMATCH" in source_quality_issues(
        _source(details="Número del artículo: 9999999"), "2513777"
    )
    assert "DETAIL_SKU_MISSING" in source_quality_issues(
        _source(details="Material: Acero"), "2513777"
    )
    assert not source_quality_issues(
        _source(details="Material\tAcero\nNúmero del artículo\t2513777"), "2513777"
    )
    assert not source_quality_issues(
        _source(details="Material Acero | Número del artículo 2513777"), "2513777"
    )


def test_consistency_gate_flags_numeric_and_unit_conflict_without_rewriting():
    flags = source_consistency_flags(
        _source(spec="20 unidades", details="Cantidad: 22 unidades; Número del artículo: 2513777")
    )
    assert "NUMERIC_CONFLICT" in flags
    assert consistency_status(flags) == "SOURCE_CONFLICT_REVIEW"


def test_consistency_gate_flags_explicit_type_material_and_size_conflicts():
    flags = source_consistency_flags(
        _source(
            name="Tipo de producto: Camisa",
            spec="Talla M",
            description="Material: Algodón",
            details=(
                "Tipo de producto: Guantes; Material: Poliéster; Talla L; "
                "Número del artículo: 2513777"
            ),
        )
    )
    assert "PRODUCT_OBJECT_CONFLICT" in flags
    assert "MATERIAL_CONFLICT" in flags
    assert "SIZE_RANGE_CONFLICT" in flags


def test_consistency_gate_flags_explicit_brand_and_model_conflicts():
    flags = source_consistency_flags(
        _source(
            spec="Marca: Acme; Modelo: X1",
            details="Marca: Other; Modelo: X2; Número del artículo: 2513777",
        )
    )
    assert "BRAND_CONFLICT" in flags
    assert "MODEL_CONFLICT" in flags


def test_historical_splits_use_selection_time_not_later_training(tmp_path):
    import json

    old = tmp_path / "20260911" / "old_train.jsonl"
    later = tmp_path / "20260913" / "new_train.jsonl"
    for path, sku in ((old, "1"), (later, "2")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"metadata": {"sku": sku, "source_hash": sku}}) + "\n", encoding="utf-8")
    as_of_selection = historical_sets(tmp_path, before_date="20260913")
    assert as_of_selection["pairs"] == {("1", "1")}
    assert historical_sets(tmp_path)["pairs"] == {("1", "1"), ("2", "2")}
