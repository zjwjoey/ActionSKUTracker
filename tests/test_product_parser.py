from action_tracker.products.parser import _normalize_detail


def test_detail_normalization_does_not_store_details_as_description():
    row = _normalize_detail({
        "sku": "1001",
        "name_es": "Producto",
        "spec_es": "Color: Azul; Material: Papel",
        "desc_es": "Color: Azul; Material: Papel",
        "details_es": "Color: Azul; Material: Papel",
    }, "https://www.action.com/es-es/p/1001/producto/")
    assert row["desc_es"] == ""
    assert row["details_es"] == "Color: Azul; Material: Papel"
    assert row["spec_es"] == ""


def test_detail_normalization_keeps_real_description_and_short_spec():
    row = _normalize_detail({
        "sku": "1002",
        "name_es": "Producto",
        "spec_es": "10 cm",
        "desc_es": "Ideal para usar en casa.",
        "details_es": "Longitud: 10 cm; Número del artículo: 1002",
    }, "https://www.action.com/es-es/p/1002/producto/")
    assert row["desc_es"] == "Ideal para usar en casa."
    assert row["spec_es"] == "10 cm"
