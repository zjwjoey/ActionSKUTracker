from copy import deepcopy

import pytest

from action_tracker.products.parser import _normalize_detail
from action_tracker.products.parser import normalize_verified_specification_rows
from action_tracker.services.normalization import normalize_official_text
from action_tracker.services.spanish_details import project_spanish_details, spanish_source_findings
from action_tracker.exporting.service import build_es_rows, build_es_projection_report


@pytest.mark.parametrize("label,prefix", [("Ancho de trabajo", "Ancho"), ("Material del cabezal de limpieza", "Material"), ("Forma de uso", "Forma"), ("Tamaño de la bolsa de basura", "Tamaño"), ("Tipo de producto de limpieza", "Tipo de producto")])
def test_legacy_compound_label_is_repaired_without_changing_value(label, prefix):
    suffix = label[len(prefix):].strip()
    damaged = f"{prefix}: {suffix}: Plástico"
    expected = f"{label}: Plástico"
    assert normalize_official_text(damaged, field="details") == expected
    assert project_spanish_details(damaged) == expected
    assert project_spanish_details(expected) == expected


def test_source_duplicates_preserved_but_display_exact_duplicates_suppressed():
    raw = "Sin lactosa: No; Sin lactosa: No; Potencia: 3680; Potencia: 3680 vatio; Material: Metal; Material: Plástico"
    assert normalize_official_text(raw, field="details") == raw
    assert project_spanish_details(raw) == "Sin lactosa: No; Potencia: 3680; Potencia: 3680 vatio; Material: Metal; Material: Plástico"


def test_arbitrary_value_colons_and_prose_remain_unchanged():
    raw = "Advertencia: atención: no abrir; Contenido: de regalo: botella; Texto sin clave"
    assert project_spanish_details(raw) == raw
    assert project_spanish_details(None) is None


def test_verified_legacy_nutrition_labels_have_delimiters_and_keep_facts():
    raw = "Carbohidratos 55 g; Contenido 144 g; Sal 0.26 g; Sin lactosa No; Sin lactosa No; Vegano No"
    expected = "Carbohidratos: 55 g; Contenido: 144 g; Sal: 0.26 g; Sin lactosa: No; Vegano: No"
    assert project_spanish_details(raw) == expected
    assert project_spanish_details(expected) == expected
    # Website changes must never enter a mechanical display repair.
    assert "144 g" in expected and "0.26 g" in expected


def test_source_corruption_stays_literal_and_is_flagged_by_extraction():
    raw = {"sku": "1001", "current_price": "1,00 €", "desc_es": "Suaves e inoloras", "details_es": "Sustancia: Válido, Loción; Sin alcohol: No", "detail_rows": [{"key": "Sustancia", "value": "Válido, Loción"}]}
    row = _normalize_detail(raw, "https://example.test/p/1001/")
    assert row["details_es"] == raw["details_es"]
    assert row["_raw_detail_rows"] == raw["detail_rows"]
    assert len(row["_source_quality_findings"]) == 2
    assert project_spanish_details(row["details_es"]) == raw["details_es"]
    assert spanish_source_findings("Texto normal", "Sustancia: Gel") == []


def test_export_parity_and_source_immutability():
    base = {"sku": "1001", "name_es": "Producto", "current_price": 3.0, "original_price": 4.0,
            "details_es": "Material: del mango: Metal; Sin lactosa: No; Sin lactosa: No", "desc_es": "Suaves e inoloras"}
    source = deepcopy(base)
    primary = build_es_rows([source])[0]
    master = build_es_rows([{**source, "discount": -0.25}])[0]
    assert primary == master
    assert "Descuento calculado: 25.00%" in primary["备注"]
    assert "Revisar texto de origen" in primary["备注"]
    report = build_es_projection_report([source], [primary])
    assert report["repaired_fields"] == 1
    assert report["source_issue_fields"] == 1
    assert report["events"][0]["before"] == base["details_es"]
    assert source == base
    assert "Sin lactosa: No; Sin lactosa: No" not in primary["产品详情"]


def test_verified_official_table_preserves_conflicting_and_duplicate_rows():
    rows = [["Potencia", "3680"], ["Potencia", "3680 vatio"], ["Sin lactosa", "No"], ["Sin lactosa", "No"], ["Número del artículo", "1001"]]
    text = normalize_verified_specification_rows(rows, expected_sku="1001")
    assert text.count("Sin lactosa: No") == 2
    assert "Potencia: 3680; Potencia: 3680 vatio" in text


@pytest.mark.parametrize("rows", [[["Número del artículo", "1002"]], [["Material", ""]], [["Potencia", "3680", "W"]]])
def test_invalid_official_table_cannot_be_applied(rows):
    with pytest.raises(ValueError):
        normalize_verified_specification_rows(rows, expected_sku="1001")
