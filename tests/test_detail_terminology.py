from __future__ import annotations

import json
import unicodedata
from pathlib import Path

from action_tracker.translation.detail_terminology import (
    repair_detail_candidate, resolve_detail_from_rules,
)


ROOT = Path(__file__).resolve().parents[1]


def _rules():
    return json.loads((ROOT / "config/stage5/detail_terminology_rules.json").read_text(encoding="utf-8"))


def _ascii_normalized(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def test_cleaning_turn_count_repairs_wrong_key_and_adds_count_suffix():
    repaired, flags = repair_detail_candidate(
        "Número de turnos de limpieza: 42",
        "清洁档位数量: 42",
        _rules(),
    )

    assert repaired == "洗涤次数: 42次"
    assert "DETAIL_KEY_RULE:0" in flags
    assert "DETAIL_NUMERIC_KEY_RULE:0" in flags


def test_article_number_key_aliases_normalize_without_changing_values_or_duplicate_order():
    rules = _rules()
    source = "Número del artículo: 3205644; Número del artículo: 3205645"
    candidate = "商品编号: 3205644; 货号: 3205645"

    repaired, flags = repair_detail_candidate(source, candidate, rules)

    assert repaired == "商品编号: 3205644; 商品编号: 3205645"
    assert "DETAIL_KEY_RULE:1" in flags


def test_article_number_rule_canonicalizes_all_observed_key_variants():
    rules = _rules()
    for alias in ("商品编号", "产品编号", "货号"):
        repaired, _ = repair_detail_candidate(
            "Número del artículo: 3222583",
            f"{alias}: 3222583",
            rules,
        )
        assert repaired == "商品编号: 3222583", alias


def test_v5_high_frequency_detail_keys_normalize_aliases_without_changing_values():
    rules = _rules()
    cases = (
        ("Color", "Rojo", "颜色"),
        ("Cantidad", "3", "数量"),
        ("Instrucciones de lavado", "Lavar a máquina", "洗涤说明"),
        ("Instrucciones de planchado", "No planchar", "熨烫说明"),
        ("Consejos sobre conservación", "Mantener seco", "保存建议"),
        ("Energía", "130 kJ", "能量"),
        ("Carbohidratos", "12 g", "碳水化合物"),
        ("De los cuales azúcares", "8 g", "其中糖分"),
        ("Sal", "0.2 g", "盐"),
        ("Grasas", "4 g", "脂肪"),
        ("Proteínas", "2 g", "蛋白质"),
        ("Edad adecuada", "3 año", "适用年龄"),
        ("De los cuales saturados", "1 g", "其中饱和脂肪"),
        ("Potencia", "60 W", "功率"),
        ("Sabor", "Fresa", "口味"),
        ("Voltaje", "9 V", "电压"),
        (
            "Medidas (incl. envase) (largo x ancho x alto)",
            "12 x 8 x 4 cm",
            "尺寸（含包装）（长×宽×高）",
        ),
        ("Longitud del cable", "1.5 m", "电线长度"),
        ("Talla de las prendas de vestir", "M", "服装尺码"),
        ("Número de pilas necesarias", "2", "所需电池数量"),
        ("Pilas incluidas", "Sí", "含电池"),
        ("Con tapa", "No", "带盖"),
        ("Apto para el lavavajillas", "Sí", "可用洗碗机清洗"),
        ("Apto para el microondas", "No", "可用于微波炉"),
    )

    for source_key, value, alias in cases:
        source = f"{source_key}: {value}"
        candidate = f"{alias}: {value}"
        repaired, flags = repair_detail_candidate(source, candidate, rules)

        canonical = rules["key_translations"][source_key.casefold()]
        assert repaired == f"{canonical}: {value}", source_key
        if alias != canonical:
            assert "DETAIL_KEY_RULE:0" in flags, source_key
        else:
            assert "DETAIL_KEY_RULE:0" not in flags, source_key


def test_cantidad_does_not_auto_accept_packaging_quantity_alias():
    source = "Cantidad: 2"
    candidate = "包装数量: 2"

    repaired, flags = repair_detail_candidate(source, candidate, _rules())

    assert repaired == candidate
    assert flags == ("DETAIL_PAIR_ALIGNMENT_UNCERTAIN",)


def test_detail_repairs_only_wrong_fragments_and_preserves_cell_separators():
    source = (
        "Número de turnos de limpieza: 42; Cubierta: Cubierta blanda; "
        "Material: Polipropileno (PP)"
    )
    candidate = "清洁档位数量: 42; 装订方式: 平装; 材料: 聚丙烯(聚丙烯)"

    repaired, flags = repair_detail_candidate(source, candidate, _rules())

    assert repaired == "洗涤次数: 42次; 封面: 软封面; 材质: 聚丙烯（PP）"
    assert "DETAIL_KEY_RULE:0" in flags
    assert "DETAIL_VALUE_RULE:1" in flags
    assert "DETAIL_KEY_RULE:2" in flags
    assert "DETAIL_VALUE_RULE:2" in flags


def test_detail_rule_does_not_rewrite_when_source_and_target_pairs_are_misaligned():
    source = "Número de turnos de limpieza: 42; Cubierta: Cubierta blanda"
    candidate = "装订方式: 平装; 清洁档位数量: 42"

    repaired, flags = repair_detail_candidate(source, candidate, _rules())

    assert repaired == candidate
    assert flags == ("DETAIL_PAIR_ALIGNMENT_UNCERTAIN",)


def test_historical_enum_errors_are_corrected_with_source_context():
    rules = _rules()
    keys = rules["key_translations"]
    aliases = rules["candidate_key_aliases"]
    values = rules["value_translations"]
    relleno = next(rule for rule in values if _ascii_normalized(rule["source_key"]) == "relleno de pagina")
    fiction = next(rule for rule in values if _ascii_normalized(rule["source_key"]) == "genero")
    fiction_key = next(
        rule for rule in rules["contextual_key_translations"]
        if _ascii_normalized(rule["source_key"]) == "genero"
    )
    dish = next(rule for rule in values if rule["source_key"] == "tipo de plato")
    animal = next(rule for rule in values if rule["source_key"] == "apto para tipo de animal (de compañía)")
    color = next(rule for rule in values if rule["source_key"] == "a color")
    cemetery = next(rule for rule in values if rule["source_key"] == "uso previsto")
    transport = next(rule for rule in rules["contextual_key_translations"] if rule["source_key"] == "modo de transporte")
    dish_wrong, dish_right = next(iter(dish["target_value_contains"].items()))
    cases = (
        (
            f"{relleno['source_key']}: {relleno['source_value']}",
            f"{aliases[relleno['source_key']][0]}: {relleno['candidate_value_any'][0]}",
            {},
            f"{keys[relleno['source_key']]}: {relleno['target_value']}",
        ),
        (
            f"{transport['source_key']}: On ear",
            f"{aliases[transport['source_key']][0]}: A",
            {"name_es": "Auriculares Bluetooth"},
            f"{transport['target_key']}: A",
        ),
        (
            f"{fiction['source_key']}: {fiction['source_value']}",
            f"{aliases[fiction['source_key']][0]}: {fiction['candidate_value_any'][0]}",
            {"name_es": "Novela juvenil"},
            f"{fiction_key['target_key']}: {fiction['target_value']}",
        ),
        (
            f"{dish['source_key']}: {dish['source_value_contains']}",
            f"{aliases[dish['source_key']][0]}: {dish_wrong}",
            {},
            f"{keys[dish['source_key']]}: {dish_right}",
        ),
        (
            f"{animal['source_key']}: Ave",
            f"{aliases[animal['source_key']][0]}: 禽肉",
            {},
            f"{keys[animal['source_key']]}: 鸟类",
        ),
        (
            f"{color['source_key']}: No",
            f"{aliases[color['source_key']][0]}: 否",
            {},
            f"{keys[color['source_key']]}: 否",
        ),
        (
            f"{cemetery['source_key']}: Fosa",
            f"{aliases[cemetery['source_key']][0]}: 坑式",
            {"name_es": "Vela funeraria"},
            f"{keys[cemetery['source_key']]}: 墓地",
        ),
    )

    for source, candidate, context, expected in cases:
        repaired, flags = repair_detail_candidate(source, candidate, rules, context=context)
        assert repaired == expected, f"source={source!r}; repaired={repaired!r}; flags={flags!r}"
        assert flags


def test_unmapped_value_for_closed_enum_key_is_routed_to_manual_review():
    source = "Tipo de batería: Alcalina"
    candidate = "电池类型: 碱性电池"

    repaired, flags = repair_detail_candidate(source, candidate, _rules())

    assert repaired == candidate
    assert "DETAIL_SOURCE_VALUE_UNMAPPED_REVIEW:0" in flags


def test_numeric_value_for_turn_count_is_not_misclassified_as_unmapped_enum():
    _, flags = repair_detail_candidate(
        "Número de turnos de limpieza: 42",
        "洗涤次数: 42次",
        _rules(),
    )

    assert not any(flag.startswith("DETAIL_SOURCE_VALUE_UNMAPPED_REVIEW:") for flag in flags)


def test_complete_rule_covered_detail_cell_can_be_rendered_without_model():
    rules = _rules()
    source = (
        "Número de turnos de limpieza: 42; A color: No; "
        "Material: Polipropileno (PP); Tipo de plato: No desechable"
    )

    translated = resolve_detail_from_rules(source, rules)

    assert translated == "洗涤次数: 42次; 是否彩色: 否; 材质: 聚丙烯（PP）; 餐具类型: 非一次性"


def test_rule_renderer_falls_back_when_any_detail_pair_is_unmapped():
    translated = resolve_detail_from_rules(
        "Tipo de batería: Alcalina; Propiedad desconocida: Valor", _rules(),
    )

    assert translated is None


def test_rule_renderer_preserves_original_delimiter_and_requires_context():
    rules = _rules()
    translated = resolve_detail_from_rules(
        "Uso previsto: Fosa | A color: No",
        rules,
        context={"name_es": "Vela funeraria"},
    )
    unrelated_context = resolve_detail_from_rules(
        "Uso previsto: Fosa | A color: No",
        rules,
        context={"name_es": "Auriculares inalámbricos"},
    )

    assert translated == "用途: 墓地 | 是否彩色: 否"
    assert unrelated_context is None


def test_unmapped_closed_enum_blocks_stage5_automatic_candidate_acceptance():
    from action_tracker.stage5.pipeline import _require_detail_manual_review
    from action_tracker.translation.model_guard import ModelOutputCheck

    check = ModelOutputCheck(True, (), {"details": ()})
    result = _require_detail_manual_review(
        check, "details", ("DETAIL_SOURCE_VALUE_UNMAPPED_REVIEW:0",),
    )

    assert not result.accepted
    assert "DETAIL_TERMINOLOGY_REVIEW" in result.field_reasons["details"]
