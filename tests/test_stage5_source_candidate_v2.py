from __future__ import annotations

from action_tracker.stage5.source_candidate_v2 import (
    FAMILY_KEY_METHOD,
    SOURCE_HASH_CONTRACT_VERSION,
    consistency_status,
    family_key,
    source_consistency_flags,
    source_consistency_evidence,
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
    source = _source(spec="20 unidades", details="Cantidad: 22 unidades; Número del artículo: 2513777")
    flags = source_consistency_flags(source)
    assert "NUMERIC_CONFLICT" in flags
    assert consistency_status(flags) == "SOURCE_CONFLICT_REVIEW"
    evidence = next(row for row in source_consistency_evidence(source) if row["code"] == "NUMERIC_CONFLICT")
    assert evidence["parsed_measurements"]["spec"]["count"] == [{"value": 20.0, "unit": "unidad"}]
    assert evidence["parsed_measurements"]["details"]["count"] == [{"value": 22.0, "unit": "unidad"}]
    assert evidence["cross_field_comparisons"] == [{
        "kind": "count", "left_field": "spec",
        "left_values": [{"value": 20.0, "unit": "unidad"}],
        "right_field": "details",
        "right_values": [{"value": 22.0, "unit": "unidad"}],
        "numeric_values_differ": True, "units_differ": False, "units_incompatible": False,
    }]


def test_consistency_gate_normalizes_inflection_and_exact_physical_unit_conversions():
    equivalent = source_consistency_flags(
        _source(spec="4 litros", details="Contenido: 4000 ml; Número del artículo: 2513777")
    )
    assert "NUMERIC_CONFLICT" not in equivalent
    assert "UNIT_CONFLICT" not in equivalent

    different_amount = source_consistency_flags(
        _source(spec="4 litros", details="Contenido: 3 ml; Número del artículo: 2513777")
    )
    assert "NUMERIC_CONFLICT" in different_amount
    assert "UNIT_CONFLICT" not in different_amount

    incompatible_count_units = source_consistency_flags(
        _source(spec="4 hojas", details="Cantidad: 4 unidades; Número del artículo: 2513777")
    )
    assert "NUMERIC_CONFLICT" in incompatible_count_units
    assert "UNIT_CONFLICT" in incompatible_count_units

    equivalent_length = source_consistency_flags(
        _source(spec="6 pulgadas", details="Longitud: 15,24 cm; Número del artículo: 2513777")
    )
    assert "NUMERIC_CONFLICT" not in equivalent_length
    assert "UNIT_CONFLICT" not in equivalent_length


def test_consistency_gate_does_not_compare_package_mass_to_nutrition_grams():
    source = _source(
        spec="3 x 100 gramos",
        details=(
            "Cantidad: paquete de 3; Carbohidratos: 58 g; Azúcares: 0,8 g; "
            "Grasas saturadas: 9 g; Grasas: 17 g; Número del artículo: 2513777"
        ),
    )

    assert "NUMERIC_CONFLICT" not in source_consistency_flags(source)
    evidence = source_consistency_evidence(source)
    assert not any(row["code"] in {"NUMERIC_CONFLICT", "UNIT_CONFLICT"} for row in evidence)


def test_consistency_gate_still_compares_explicit_package_quantity_labels():
    source = _source(
        spec="20 unidades",
        details="Cantidad: 22 unidades; Número del artículo: 2513777",
    )

    assert "NUMERIC_CONFLICT" in source_consistency_flags(source)
    evidence = next(row for row in source_consistency_evidence(source) if row["code"] == "NUMERIC_CONFLICT")
    assert evidence["cross_field_comparisons"][0]["right_values"] == [
        {"value": 22.0, "unit": "unidad"}
    ]


def test_consistency_gate_compares_pack_total_to_labeled_net_content():
    equivalent = _source(
        spec="6x 33,3 gramos",
        details=(
            "Cantidad: 6 unidades; Contenido: 200 g; Carbohidratos: 55 g; "
            "Grasas: 18 g; Número del artículo: 2513777"
        ),
    )
    conflict = _source(
        spec="6x 33,3 gramos",
        details="Cantidad: 6 unidades; Contenido: 250 g; Número del artículo: 2513777",
    )

    assert "NUMERIC_CONFLICT" not in source_consistency_flags(equivalent)
    assert "NUMERIC_CONFLICT" in source_consistency_flags(conflict)
    evidence = next(
        row for row in source_consistency_evidence(conflict)
        if row["code"] == "NUMERIC_CONFLICT"
    )
    candidate_values = evidence["cross_field_comparisons"][0]["left_values"]
    assert {row["unit"] for row in candidate_values} == {"g"}
    assert any(abs(row["value"] - 199.8) < 1e-9 for row in candidate_values)
    assert evidence["cross_field_comparisons"][0]["left_basis"] == (
        "package_count_or_item_amount_or_package_total"
    )


def test_consistency_gate_does_not_mix_drying_length_with_product_dimensions():
    source = _source(
        spec="58x73x29 cm",
        details=(
            "Longitud de secado: 600 cm; Número del artículo: 2513777"
        ),
    )

    assert "NUMERIC_CONFLICT" not in source_consistency_flags(source)


def test_consistency_gate_accepts_overlapping_explicit_dimension_evidence():
    source = _source(
        spec="24 cm",
        details="Medidas: 24 cm; Ancho: 28 cm; Número del artículo: 2513777",
    )

    assert "NUMERIC_CONFLICT" not in source_consistency_flags(source)


def test_consistency_gate_normalizes_piece_and_unit_count_aliases():
    source = _source(
        spec="3 piezas",
        details="Cantidad: 3 unidades; Número del artículo: 2513777",
    )

    assert "NUMERIC_CONFLICT" not in source_consistency_flags(source)
    assert "UNIT_CONFLICT" not in source_consistency_flags(source)


def test_consistency_gate_handles_multipack_count_as_pack_or_total_quantity():
    pack_count = _source(
        spec="2x 500 unidades",
        details="Cantidad: 2 unidades; Número del artículo: 2513777",
    )
    package_total = _source(
        spec="3x 1000 unidades",
        details="Cantidad: 3000 unidades; Número del artículo: 2513777",
    )
    sheet_total = _source(
        spec="4x 100 hojas",
        details=(
            "Cantidad: 400 unidades; Número de hojas: 400; "
            "Número del artículo: 2513777"
        ),
    )

    assert "NUMERIC_CONFLICT" not in source_consistency_flags(pack_count)
    assert "NUMERIC_CONFLICT" not in source_consistency_flags(package_total)
    assert "NUMERIC_CONFLICT" not in source_consistency_flags(sheet_total)


def test_consistency_gate_does_not_compare_packaged_dimensions_to_product_dimensions():
    source = _source(
        spec="21x30 cm",
        details=(
            "Medidas (incl. envase) (largo x ancho x alto): 23.5 x 32.2 cm; "
            "Número del artículo: 2513777"
        ),
    )

    assert "NUMERIC_CONFLICT" not in source_consistency_flags(source)


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


def test_configured_source_anomaly_rules_are_applied_and_keep_field_evidence():
    source = _source(details="Sustancia: Válido; Número del artículo: 2513777")

    flags = source_consistency_flags(source)
    evidence = source_consistency_evidence(source)

    assert "SOURCE_ANOMALY_SUSTANCIA_VALIDO" in flags
    assert any(
        row["code"] == "SOURCE_ANOMALY_SUSTANCIA_VALIDO"
        and row["source_key"] == "Sustancia"
        and row["source_value"] == "Válido"
        for row in evidence
    )


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
