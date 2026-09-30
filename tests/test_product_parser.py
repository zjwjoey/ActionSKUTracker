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


def test_field_misplacement_keeps_raw_source_and_marks_anomaly():
    raw = "Color: Azul; Material: Papel"
    row = _normalize_detail({
        "sku": "1003", "name_es": "Producto", "spec_es": raw,
        "desc_es": raw, "details_es": raw,
    }, "https://www.action.com/es-es/p/1003/producto/")
    assert row["spec_es_raw"] == raw
    assert row["desc_es_raw"] == raw
    assert "DESCRIPTION_DUPLICATES_DETAILS" in row["source_anomalies"]
    assert "SPEC_LOOKS_LIKE_DETAILS_TABLE" in row["source_anomalies"]
    assert row["source_quality"] == "SOURCE_SUSPECT"


def test_detail_categories_keep_official_breadcrumb_values():
    row = _normalize_detail({
        "sku": "3218264", "name_es": "Guirnalda de luz navideña Luxuriance Lights",
        "cat1_es": "Vivienda", "cat2_es": "Decoración",
    }, "https://www.action.com/es-es/p/3218264/guirnalda-de-luz-navidena-luxuriance-lights/")
    assert row["cat1_es"] == "Vivienda"
    assert row["cat2_es"] == "Decoración"
    assert "CATEGORY_PRIMARY_INVALID" not in row["source_anomalies"]


def test_detail_categories_reject_title_and_navigation_pollution():
    row = _normalize_detail({
        "sku": "3218264", "name_es": "Guirnalda de luz navideña Luxuriance Lights",
        "cat1_es": "Guirnalda de luz navideña Luxuriance Lights", "cat2_es": "Atrás",
    }, "https://www.action.com/es-es/p/3218264/guirnalda-de-luz-navidena-luxuriance-lights/")
    assert row["cat1_es"] == ""
    assert row["cat2_es"] == ""
    assert "CATEGORY_PRIMARY_INVALID" in row["source_anomalies"]
    assert "CATEGORY_SECONDARY_INVALID" in row["source_anomalies"]


def test_detail_category_may_legitimately_match_product_title():
    row = _normalize_detail({
        "sku": "3224775", "name_es": "Galletas",
        "cat1_es": "Comer y beber", "cat2_es": "Galletas",
    }, "https://www.action.com/es-es/p/3224775/galletas/")
    assert row["cat1_es"] == "Comer y beber"
    assert row["cat2_es"] == "Galletas"
    assert "CATEGORY_SECONDARY_INVALID" not in row["source_anomalies"]
