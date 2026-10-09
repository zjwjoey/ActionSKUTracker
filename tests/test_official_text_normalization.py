from action_tracker.products.parser import _normalize_detail
from action_tracker.services.normalization import normalize_official_text


def test_spec_size_labels_keep_literal_ranges_and_do_not_consume_other_words():
    from action_tracker.localization.formatter import format_spec
    assert format_spec('Tallas 98-140 | diferentes variantes') == '尺码 98–140｜多款可选'
    assert format_spec('Talla XL | 2 unidades') == '尺码 XL｜2 件'
    assert format_spec('Tallas 240-255 cm | 2 piezas') == '尺码 240–255cm｜2 件'
    assert format_spec('Pantallas 2 unidades') == 'Pantallas 2 件'


def test_numeric_qa_does_not_merge_a_thousands_group_across_fields():
    from action_tracker.localization.contracts import SourceFacts
    from action_tracker.localization.qa import guard_translation
    facts=SourceFacts.from_record({'sku':'1001','name_es':'Modelo 1','spec_es':'300 ml'})
    assert guard_translation(facts,{'spec':'300ml'},('spec',))['status']=='PASS'
    assert guard_translation(facts,{'spec':'301ml'},('spec',))['status']=='FAIL'
    assert guard_translation(facts,{'spec':'1300ml'},('spec',))['status']=='FAIL'


def test_battery_capacity_spacing_is_equivalent_but_value_and_unit_are_protected():
    from action_tracker.localization.contracts import SourceFacts
    from action_tracker.localization.qa import guard_translation
    for source,target in [('5000 mAh','5000mAh'),('5000mAh','5000 mAh')]:
        facts=SourceFacts.from_record({'sku':'1001','spec_es':source})
        assert guard_translation(facts,{'spec':target},('spec',))['status']=='PASS'
        assert guard_translation(facts,{'spec':'6000mAh'},('spec',))['status']=='FAIL'
        assert guard_translation(facts,{'spec':'5000Ah'},('spec',))['status']=='FAIL'


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


def test_compound_detail_keys_are_not_split_by_known_shorter_prefixes():
    raw="Material: Plástico; Material estructura: Plástico; Forma de la brocha: Pincel; Tipo de productos para el hogar: Limpieza; Material cabello: Sintético"
    assert normalize_official_text(raw,field="details")==raw
    assert normalize_official_text("Materiales diversos",field="details")=="Materiales diversos"


def test_historical_detail_literal_parser_preserves_duplicate_keys():
    from action_tracker.localization.normalization.structured_details import parse_structured_details
    pairs=parse_structured_details("{'Material': 'Plástico', 'Material': 'Metal', 'Sin alcohol': 'No'}")
    assert [(p.key,p.value) for p in pairs]==[("Material","Plástico"),("Material","Metal"),("Sin alcohol","No")]


def test_boolean_detail_qa_checks_negative_label_truth():
    from action_tracker.localization.contracts import SourceFacts
    from action_tracker.localization.qa import guard_translation
    source=SourceFacts(sku="1001",details_es="{'Sin alcohol': 'No'}")
    bad=guard_translation(source,{"details":"{'是否含酒精': '否'}"},("details",))
    assert any(f["rule_id"]=="DETAIL_BOOLEAN_POLARITY_CHANGED" for f in bad["findings"])
    assert guard_translation(source,{"details":"{'是否无酒精': '否'}"},("details",))["status"]=="PASS"
    assert guard_translation(source,{"details":"{'是否含酒精': '是'}"},("details",))["status"]=="PASS"


def test_accented_spanish_nouns_are_not_units_but_chinese_adjacent_units_are():
    from action_tracker.localization.contracts import SourceFacts
    from action_tracker.localization.qa import guard_translation
    source=SourceFacts(sku="1001",spec_es="12 lápices")
    assert guard_translation(source,{"spec":"12 支铅笔"},("spec",))["status"]=="PASS"
    source=SourceFacts(sku="1001",spec_es="5 cm")
    assert guard_translation(source,{"spec":"5cm宽"},("spec",))["status"]=="PASS"
    assert any(f["rule_id"]=="UNIT_DROPPED" for f in guard_translation(source,{"spec":"5宽"},("spec",))["findings"])


def test_duster_material_conflict_is_blocked():
    from action_tracker.localization.contracts import SourceFacts
    from action_tracker.localization.qa import guard_translation
    source=SourceFacts(sku="1001",name_es="Plumero de microfibras",details_es="Tipo: Plumero")
    assert any(f["rule_id"]=="MATERIAL_CONFLICT_WITH_SOURCE" for f in guard_translation(source,{"details":"类型：鸡毛掸子"},("details",))["findings"])
    assert guard_translation(source,{"details":"类型：除尘掸"},("details",))["status"]=="PASS"


def test_nonsterile_claim_cannot_be_reversed_or_omitted():
    from action_tracker.localization.contracts import SourceFacts
    from action_tracker.localization.qa import guard_translation
    source=SourceFacts(sku="1001",desc_es="Guantes de látex y no estériles")
    assert any(f["rule_id"]=="STERILITY_STATUS_CHANGED" for f in guard_translation(source,{"description":"无菌乳胶手套"},("description",))["findings"])
    assert any(f["rule_id"]=="STERILITY_STATUS_DROPPED" for f in guard_translation(source,{"description":"乳胶手套"},("description",))["findings"])
    assert guard_translation(source,{"description":"非无菌乳胶手套"},("description",))["status"]=="PASS"


def test_brush_bristle_key_is_not_translated_as_human_hair():
    from action_tracker.localization.contracts import SourceFacts
    from action_tracker.localization.qa import guard_translation
    source=SourceFacts(sku="1001",name_es="Juego de pinceles",details_es="Material cabello: Sintético")
    assert any(f["rule_id"]=="DETAIL_SUBJECT_CHANGED" for f in guard_translation(source,{"details":"发丝材质：合成"},("details",))["findings"])
    assert guard_translation(source,{"details":"刷毛材质：合成"},("details",))["status"]=="PASS"


def test_dual_color_numeric_equivalence_is_scoped_and_counted():
    from action_tracker.localization.contracts import SourceFacts
    from action_tracker.localization.qa import guard_translation
    source=SourceFacts(sku="1001",desc_es="2 tonos")
    assert guard_translation(source,{"description":"双色"},("description",))["status"]=="PASS"
    source=SourceFacts(sku="1001",desc_es="3 tonos")
    assert guard_translation(source,{"description":"双色"},("description",))["status"]=="FAIL"
    source=SourceFacts(sku="1001",desc_es="2 tonos; 2 unidades")
    assert guard_translation(source,{"description":"双色"},("description",))["status"]=="FAIL"


def test_no_ironing_instruction_is_not_optional_or_an_easy_care_claim():
    from action_tracker.localization.contracts import SourceFacts
    from action_tracker.localization.qa import guard_translation
    source=SourceFacts(sku="1001",details_es="Instrucciones de planchado: Sin planchado")
    assert any(f["rule_id"]=="CARE_INSTRUCTION_CHANGED" for f in guard_translation(source,{"details":"熨烫说明：无需熨烫"},("details",))["findings"])
    assert guard_translation(source,{"details":"熨烫说明：不熨烫"},("details",))["status"]=="PASS"
