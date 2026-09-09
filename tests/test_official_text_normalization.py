from action_tracker.products.parser import _normalize_detail
from action_tracker.services.normalization import normalize_official_text


def test_normalizer_removes_ui_transport_residue_without_changing_facts():
    assert normalize_official_text("Añadir a tus favoritos", field="spec") is None
    assert normalize_official_text("Descripción\n<a href='x'>Texto</a>\nLeer más", field="description") == "Texto"
    assert normalize_official_text("Material:: Plástico; Número del artículo; 2536376", field="details") == (
        "Material: Plástico; Número del artículo: 2536376"
    )


def test_normalizer_preserves_repeated_official_detail_fields():
    value = normalize_official_text(
        "Longitud del cable:: 20 m; Longitud del cable:: 20; Potencia: 3.6",
        field="details",
    )
    assert value == "Longitud del cable: 20 m; Longitud del cable: 20; Potencia: 3.6"


def test_detail_parser_leaves_original_price_blank_without_official_original_price():
    row = _normalize_detail(
        {
            "sku": "1001",
            "name_es": "Producto",
            "current_price": "3,99 €",
            "original_price": "",
            "spec_es": "Añadir a tus favoritos",
        },
        "https://example.test/p/1001/",
    )
    assert row["current_price"] == 3.99
    assert row["original_price"] is None
    assert row["spec_es"] == ""
