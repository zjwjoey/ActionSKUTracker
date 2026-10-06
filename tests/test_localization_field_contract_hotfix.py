from action_tracker.localization.contracts import (
    CANONICAL_AI_FIELDS,
    CANONICAL_TO_SOURCE,
    CANONICAL_TO_ZH,
    LOCALIZATION_FIELD_CONTRACT,
    SourceFacts,
    LocalizationField,
    LocalizationPlan,
    SemanticFact,
    source_hash,
)
from action_tracker.localization.ai import validate_ai_response
from action_tracker.localization.engine import LocalizationEngine
from action_tracker.localization.learning import aggregate_candidates, promotion_decision, STATES
from action_tracker.knowledge.validator import validate_candidate
from action_tracker.localization.promotion import can_promote, KnowledgePromotionRouter, KnowledgePromotionError
from action_tracker.localization.ai import FakeProvider, extract_technical_tokens


def test_canonical_field_contract_is_reversible_for_all_seven_fields():
    assert tuple(LOCALIZATION_FIELD_CONTRACT) == (
        "name_zh", "cat1_zh", "cat2_zh", "spec_zh", "unit_price_zh", "desc_zh", "details_zh"
    )
    for zh_field, contract in LOCALIZATION_FIELD_CONTRACT.items():
        canonical = contract["canonical"]
        assert CANONICAL_TO_ZH[canonical] == zh_field
        assert CANONICAL_TO_SOURCE[canonical] == contract["source"]


def test_damaged_description_never_becomes_desc_or_description_request(tmp_path):
    from action_tracker.localization.service import audit_current
    from action_tracker.localization.knowledge import ensure_schemas
    import csv, json

    directory = tmp_path / "dict"
    ensure_schemas(directory)
    with (directory / "source_damage_report.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=("sku", "damaged_fields", "status"))
        writer.writeheader(); writer.writerow({"sku": "D1", "damaged_fields": "desc_es", "status": "SOURCE_DAMAGED"})
    cfg = {"project_root": tmp_path, "paths": {"temp": tmp_path / "runtime" / "temp", "dictionary_baseline": directory},
           "localization": {"ai": {"enabled": True, "provider": "fake"}}}
    cfg["paths"]["temp"].mkdir(parents=True)
    result = audit_current(cfg, run_id="contract-damage", records=[
        {"sku": "D1", "name_es": "Producto xyz", "cat1_es": "Hogar", "spec_es": "10 gramos", "desc_es": "texto roto"}
    ])
    rows = json.loads(open(result["ai_candidates"], encoding="utf-8").read())
    assert rows and set(rows[0]["requested_fields"]) <= set(CANONICAL_AI_FIELDS)
    assert "description" not in rows[0]["requested_fields"] and "desc" not in rows[0]["requested_fields"]


def test_non_damaged_description_is_canonical_and_numeric_guarded():
    source = SourceFacts.from_record({"sku": "D2", "name_es": "Producto", "desc_es": "Adecuado para superficies de 10 a 20 m²"})
    good = {"fields": {"description": "适用于10–20m²表面"}, "confidence": 0.99}
    bad = {"fields": {"description": "适用于表面"}, "confidence": 0.99}
    assert validate_ai_response(good, source, ("description",))[0]
    ok, reasons = validate_ai_response(bad, source, ("description",))
    assert not ok and "AI_DESCRIPTION_NUMBER_DROPPED" in reasons


def test_manual_override_rebuilds_resolvable_reasons():
    source = SourceFacts.from_record({"sku": "M1", "name_es": "Producto", "cat1_es": "Hogar"})
    facts = (SemanticFact("PRODUCT_TYPE", "Producto", "商品", canonical_value="商品"),)
    fields = {
        "name_zh": LocalizationField("测试商品", "manual_override", "READY", source.source_hash),
        "cat1_zh": LocalizationField("家居布置", "dictionary", "READY", source.source_hash),
        "cat2_zh": LocalizationField("", "missing", "READY", source.source_hash),
        "spec_zh": LocalizationField("", "missing", "READY", source.source_hash),
        "unit_price_zh": LocalizationField("", "official_unit_price", "READY", source.source_hash),
        "desc_zh": LocalizationField("", "missing", "READY", source.source_hash),
        "details_zh": LocalizationField("", "missing", "READY", source.source_hash),
    }
    plan = LocalizationPlan(source.sku, source.source_hash, fields, facts, "REVIEW_REQUIRED", ("NAME_REVIEW", "SPANISH_RESIDUAL"))
    result = LocalizationEngine().validate(source.as_record(), plan)
    assert "NAME_REVIEW" not in result.reasons and "SPANISH_RESIDUAL" not in result.reasons


def test_populated_dictionary_name_does_not_require_product_type_fact():
    """A valid existing name is enough for identity-level validation."""
    source = SourceFacts.from_record({"sku": "DICT-NAME", "name_es": "Producto sin término", "cat1_es": "Hogar"})
    fields = {
        "name_zh": LocalizationField("已确认商品", "dictionary", "READY", source.source_hash),
        "cat1_zh": LocalizationField("家居布置", "dictionary", "READY", source.source_hash),
        "cat2_zh": LocalizationField("", "missing", "READY", source.source_hash),
        "spec_zh": LocalizationField("", "missing", "READY", source.source_hash),
        "unit_price_zh": LocalizationField("", "official_unit_price", "READY", source.source_hash),
        "desc_zh": LocalizationField("", "missing", "READY", source.source_hash),
        "details_zh": LocalizationField("", "missing", "READY", source.source_hash),
    }
    plan = LocalizationPlan(source.sku, source.source_hash, fields, (), "READY", ())
    result = LocalizationEngine().validate(source.as_record(), plan)
    assert "PRODUCT_TYPE_REVIEW" not in result.reasons


def test_bad_manual_override_remains_blocked():
    source = SourceFacts.from_record({"sku": "M2", "name_es": "Producto", "cat1_es": "Hogar"})
    plan = LocalizationEngine().resolve({"sku": "M2", "name_es": "Producto", "cat1_es": "Hogar"})
    fields = dict(plan.fields)
    fields["name_zh"] = LocalizationField("producto", "manual_override", "READY", source.source_hash)
    bad = LocalizationPlan(plan.sku, plan.source_hash, fields, plan.semantic_facts, plan.readiness, plan.review_reasons)
    result = LocalizationEngine().validate(source.as_record(), bad)
    assert "SPANISH_RESIDUAL" in result.reasons


def test_ai_technical_tokens_must_be_preserved():
    source = SourceFacts.from_record({"sku": "T1", "name_es": "Lámpara LED USB-C"})
    ok = validate_candidate({"sku": "T1", "source_hash": source_hash(source.as_record()), "fields": {"name": "LED USB-C灯"}}, source.as_record())
    assert ok.ok
    bad = validate_candidate({"sku": "T1", "source_hash": source_hash(source.as_record()), "fields": {"name": "灯"}}, source.as_record())
    assert not bad.ok and "TECH_TOKEN_DROPPED" in bad.reasons


def test_evidence_conflict_is_stateful_and_blocks_promotion(tmp_path):
    rows = [
        {"sku": "C1", "semantic_type": "TECH_TOKEN", "source_term": "LED", "zh_value": "LED", "source_hash": "h1"},
        {"sku": "C1", "semantic_type": "TECH_TOKEN", "source_term": "LED", "zh_value": "LED", "source_hash": "h2"},
    ]
    candidate = aggregate_candidates(rows, tmp_path)["rows"][0]
    assert "EVIDENCE_CONFLICT" in STATES and candidate["status"] == "EVIDENCE_CONFLICT"
    decision = promotion_decision(candidate)
    assert not decision["promoted"] and decision["promotion_blocked"] is True


def _write_override(directory, sku, field, value):
    import csv
    from action_tracker.dictionary import OVERRIDE_HEADERS
    with (directory / "manual_overrides.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=OVERRIDE_HEADERS)
        writer.writeheader()
        writer.writerow({"scope": "product", "key": sku, "field": field, "value": value, "source": "TEST"})


def test_manual_override_is_terminal_and_clears_product_type_review(tmp_path):
    from action_tracker.localization.knowledge import ensure_schemas
    from action_tracker.localization.service import audit_current
    import csv

    directory = tmp_path / "dict"; ensure_schemas(directory)
    _write_override(directory, "MANUAL-GOOD", "name_zh_standard", "测试商品")
    cfg = {"project_root": tmp_path, "paths": {"temp": tmp_path / "runtime" / "temp", "dictionary_baseline": directory}}
    cfg["paths"]["temp"].mkdir(parents=True)
    record = {"sku": "MANUAL-GOOD", "name_es": "Producto desconocido", "cat1_es": "家居布置", "spec_es": "10 gramos"}
    result = audit_current(cfg, run_id="manual-terminal", records=[record])
    row = next(csv.DictReader(open(result["audit"], encoding="utf-8-sig")))
    assert row["new_name_zh"] == "测试商品"
    assert row["readiness"] == "READY"
    assert not {"PRODUCT_TYPE_REVIEW", "NAME_REVIEW", "SPANISH_RESIDUAL"} & set(filter(None, row["review_reasons"].split("|")))


def test_manual_field_is_excluded_from_ai_and_other_unknown_field_is_requested(tmp_path, monkeypatch):
    from action_tracker.localization.knowledge import ensure_schemas
    from action_tracker.localization.service import audit_current
    import json

    directory = tmp_path / "dict"; ensure_schemas(directory)
    _write_override(directory, "MANUAL-AI", "name_zh_standard", "测试商品")
    provider = FakeProvider({"MANUAL-AI": {"fields": {"description": "适用于10m²"}, "confidence": 0.99}})
    monkeypatch.setattr("action_tracker.localization.service.provider_from_config", lambda _config: provider)
    cfg = {"project_root": tmp_path, "paths": {"temp": tmp_path / "runtime" / "temp", "dictionary_baseline": directory},
           "localization": {"ai": {"enabled": True, "provider": "fake"}}}
    cfg["paths"]["temp"].mkdir(parents=True)
    record = {"sku": "MANUAL-AI", "name_es": "Producto desconocido", "cat1_es": "家居布置", "spec_es": "10 gramos",
              "desc_es": "Adecuado para 10 m²"}
    result = audit_current(cfg, run_id="manual-ai-terminal", records=[record])
    candidates = json.loads(open(result["ai_candidates"], encoding="utf-8").read())
    assert provider.calls == 1
    assert candidates and candidates[0]["requested_fields"] == ["description"]


def test_manual_only_resolved_sku_makes_zero_ai_calls_even_without_product_type(tmp_path, monkeypatch):
    from action_tracker.localization.knowledge import ensure_schemas
    from action_tracker.localization.service import audit_current

    directory = tmp_path / "dict"; ensure_schemas(directory)
    _write_override(directory, "MANUAL-ONLY", "name_zh_standard", "测试商品")
    provider = FakeProvider({})
    monkeypatch.setattr("action_tracker.localization.service.provider_from_config", lambda _config: provider)
    cfg = {"project_root": tmp_path, "paths": {"temp": tmp_path / "runtime" / "temp", "dictionary_baseline": directory},
           "localization": {"ai": {"enabled": True, "provider": "fake"}}}
    cfg["paths"]["temp"].mkdir(parents=True)
    record = {"sku": "MANUAL-ONLY", "name_es": "Producto desconocido", "cat1_es": "家居布置", "spec_es": "10 gramos"}
    result = audit_current(cfg, run_id="manual-only-terminal", records=[record])
    assert result["ai_call_count"] == 0


def test_existing_clean_name_does_not_reopen_product_type_review(tmp_path):
    from action_tracker.localization.knowledge import ensure_schemas
    from action_tracker.localization.service import audit_current
    import csv

    directory = tmp_path / "dict"; ensure_schemas(directory)
    _write_override(directory, "KNOWN-NAME", "name_zh_standard", "已确认商品")
    cfg = {"project_root": tmp_path, "paths": {"temp": tmp_path / "runtime" / "temp", "dictionary_baseline": directory}}
    cfg["paths"]["temp"].mkdir(parents=True)
    result = audit_current(cfg, run_id="known-name-terminal", records=[
        {"sku": "KNOWN-NAME", "name_es": "Producto sin término conocido", "cat1_es": "Hogar", "spec_es": "10 gramos"}
    ])
    row = next(csv.DictReader(open(result["audit"], encoding="utf-8-sig")))
    assert row["new_name_zh"] == "已确认商品"
    assert "PRODUCT_TYPE_REVIEW" not in set(filter(None, row["review_reasons"].split("|")))


def test_manual_spec_numeric_mismatch_remains_blocked_and_cache_cannot_replace_it(tmp_path):
    from action_tracker.localization.knowledge import ensure_schemas
    from action_tracker.localization.service import audit_current
    import csv

    directory = tmp_path / "dict"; ensure_schemas(directory)
    _write_override(directory, "MANUAL-NUM", "spec_zh_standard", "20g")
    cfg = {"project_root": tmp_path, "paths": {"temp": tmp_path / "runtime" / "temp", "dictionary_baseline": directory}}
    cfg["paths"]["temp"].mkdir(parents=True)
    record = {"sku": "MANUAL-NUM", "name_es": "Producto", "cat1_es": "家居布置", "spec_es": "10 gramos"}
    result = audit_current(cfg, run_id="manual-numeric", records=[record])
    row = next(csv.DictReader(open(result["audit"], encoding="utf-8-sig")))
    assert row["new_spec_zh"] == "20g"
    assert row["readiness"] == "REVIEW_REQUIRED"
    assert "NUMERIC_FACT_MISMATCH" in row["review_reasons"]


def test_evidence_conflict_is_hard_blocked_by_decision_and_router_without_file_change(tmp_path):
    from action_tracker.localization.knowledge import ensure_schemas, NEW_SCHEMAS
    import hashlib

    candidate = {"candidate_id": "conflict", "status": "EVIDENCE_CONFLICT", "semantic_type": "TECH_TOKEN",
                 "source_term": "LED", "zh_value": "LED", "source_hash": "h"}
    ok, reasons = can_promote(candidate, validator_pass=True, source_hash_match=True, human_approved=True)
    assert not ok and reasons == ("EVIDENCE_CONFLICT",)
    ensure_schemas(tmp_path)
    before = {name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() for name in NEW_SCHEMAS}
    try:
        KnowledgePromotionRouter(tmp_path, freshness_checker=lambda _candidate: (True, "PASS")).promote(candidate, human_approved=True)
    except KnowledgePromotionError as exc:
        assert str(exc) == "EVIDENCE_CONFLICT"
    else:
        raise AssertionError("conflicting evidence was promoted")
    after = {name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() for name in NEW_SCHEMAS}
    assert after == before


def test_anti_edad_is_not_forced_as_technical_token():
    assert "anti-edad" not in extract_technical_tokens("Crema anti-edad")
    source = {"sku": "TECH-ES", "name_es": "Crema anti-edad"}
    candidate = {"sku": "TECH-ES", "source_hash": source_hash(source), "fields": {"name": "抗衰老霜"}}
    assert validate_candidate(candidate, source).ok


def test_numeric_guard_accepts_scoped_semantic_equivalents_and_size_lists():
    from action_tracker.localization.qa import audit_translation

    source = SourceFacts.from_record({
        "sku": "NUM-EQUIV",
        "name_es": "Edredón 4 estaciones",
        "spec_es": "39,40,41,42; 9'5x13 cm",
    })
    findings = audit_translation(
        source,
        {"name": "四季被", "spec": "39、40、41、42；9.5×13cm"},
        ("name", "spec"),
    )
    assert not any(item.rule_id in {"NUMERIC_DROPPED", "NUMERIC_ADDED"} for item in findings)


def test_numeric_guard_does_not_globally_waive_unrelated_numbers():
    from action_tracker.localization.qa import audit_translation

    source = SourceFacts.from_record({"sku": "NUM-BLOCK", "name_es": "Producto 4 unidades"})
    findings = audit_translation(source, {"name": "商品"}, ("name",))
    assert any(item.rule_id == "NUMERIC_DROPPED" for item in findings)


def test_numeric_guard_ignores_digit_inside_removed_7up_brand_only():
    from action_tracker.localization.qa import audit_translation

    source = SourceFacts.from_record({"sku": "BRAND-7UP", "name_es": "7Up"})
    findings = audit_translation(source, {"name": "柠檬青柠汽水"}, ("name",))
    assert not any(item.rule_id == "NUMERIC_DROPPED" for item in findings)


def test_empty_source_is_not_a_required_translation():
    from action_tracker.localization.qa import audit_translation

    source = SourceFacts.from_record({"sku": "EMPTY-SOURCE", "description": ""})
    findings = audit_translation(source, {"description": ""}, ("description",))
    assert findings == ()


def test_nonempty_target_without_source_is_blocked():
    from action_tracker.localization.qa import audit_translation

    source = SourceFacts.from_record({"sku": "EMPTY-SOURCE-TARGET", "description": ""})
    findings = audit_translation(source, {"description": "凭空生成"}, ("description",))
    assert any(item.rule_id == "EMPTY_SOURCE_TARGET_NONEMPTY" for item in findings)


def test_brand_display_policy_removes_latin_brand_adjacent_to_chinese():
    from action_tracker.localization.policy import strip_forbidden_display_tokens

    assert strip_forbidden_display_tokens("Spargo的湿巾", ["Spargo"]) == "的湿巾"
    assert strip_forbidden_display_tokens("Spargo牌湿巾", ["Spargo"]) == "湿巾"


def test_fact_qa_accepts_source_bound_localized_technical_abbreviations():
    from action_tracker.localization.qa import audit_translation

    source = SourceFacts.from_record({
        "sku": "TECH-ALIASES",
        "desc_es": "Full HD; PH-neutro; personajes de TV",
        "details_es": "Uso previsto: WC; Relleno GSM: 350 g; Sin BPA: Sí",
    })
    findings = audit_translation(
        source,
        {
            "description": "全高清；pH值中性；电视角色",
            "details": "适用对象：马桶；填充克重（克/平方米）：350 g；不含双酚A：是",
        },
        ("description", "details"),
    )
    assert not any(item.rule_id in {"PROTECTED_TOKEN_MISSING", "PROTECTED_TOKEN_CHANGED"} for item in findings)


def test_fact_qa_does_not_treat_reviewed_uppercase_spanish_values_as_models():
    from action_tracker.localization.qa import audit_translation

    source = SourceFacts.from_record({
        "sku": "UPPER-VALUES",
        "details_es": "Tipo: CALCULADORA; Tipo de silla: TABURETE; Tipo: EDREDÓN",
    })
    findings = audit_translation(
        source,
        {"details": "类型：计算器；椅子类型：凳子；类型：被子"},
        ("details",),
    )
    assert not any(item.rule_id in {"PROTECTED_TOKEN_MISSING", "PROTECTED_TOKEN_CHANGED"} for item in findings)


def test_fact_qa_does_not_require_accent_split_uppercase_spanish_labels():
    from action_tracker.localization.qa import audit_translation

    source = SourceFacts.from_record({
        "sku": "UPPER-ACCENTED-VALUE",
        "details_es": "Tipo: ESTUFA DE LEÑA (ELÉCTRICA); Función: PREVENCIÓN DE LA PÉRDIDA DE COLOR; Clase: ÓPTICO",
    })
    findings = audit_translation(
        source,
        {"details": "类型：电动木材烧灼工具；功能：防止褪色；类别：光学型"},
        ("details",),
    )
    assert not any(item.rule_id in {"PROTECTED_TOKEN_MISSING", "PROTECTED_TOKEN_CHANGED"} for item in findings)


def test_fact_qa_translates_accent_split_uppercase_prefixes_and_lexical_values():
    from action_tracker.localization.qa import audit_translation

    source = SourceFacts.from_record({
        "sku": "UPPER-PREFIXES",
        "details_es": "Tipo: FUNDA PARA EL COLCHÓN; Tipo: MICRÓFONO DINÁMICO; Tipo: BOLSA PARA PORTÁTIL; Tipo: TABLERO MAGNÉTICO; Tipo: SET DE LIMPIEZA; Intensidad: 30 LUX; Tipo: PUFF; Fijación: CLAVIJA",
    })
    findings = audit_translation(
        source,
        {"details": "类型：床垫保护套；类型：动圈麦克风；类型：笔记本电脑包；类型：磁性板；类型：清洁套装；亮度：30勒克斯；类型：蒲团；固定件：插头"},
        ("details",),
    )
    assert not any(item.rule_id in {"PROTECTED_TOKEN_MISSING", "PROTECTED_TOKEN_CHANGED"} for item in findings)


def test_numeric_guard_normalizes_grouped_thousands_and_chinese_classifier():
    from action_tracker.localization.qa import audit_translation

    source = SourceFacts.from_record({
        "sku": "GROUPED-THOUSANDS",
        "description": "23 500 movimientos por minuto; incluye 2 brochas",
    })
    findings = audit_translation(source, {"description": "每分钟23500次摆动；包含两把刷子"}, ("description",))
    assert not any(item.rule_id == "NUMERIC_DROPPED" for item in findings)


def test_numeric_guard_does_not_join_a_model_suffix_to_next_line_number():
    from action_tracker.localization.qa import audit_translation

    source = SourceFacts.from_record({"sku": "MODEL-NUMBER", "description": "Formato B5\n192 páginas"})
    findings = audit_translation(source, {"description": "B5尺寸，内含192页"}, ("description",))
    assert not any(item.rule_id == "NUMERIC_DROPPED" for item in findings)


def test_fact_qa_accepts_chinese_area_and_ordinal_rendering():
    from action_tracker.localization.qa import audit_translation

    source = SourceFacts.from_record({"sku": "AREA-ORDINAL", "name_es": "Producto N.º 1", "description": "Cubre 25 m²"})
    findings = audit_translation(source, {"name": "商品一号", "description": "覆盖面积25平方米"}, ("name", "description"))
    assert not any(item.rule_id in {"NUMERIC_DROPPED", "NUMERIC_ADDED", "UNIT_DROPPED"} for item in findings)


def test_fact_qa_keeps_short_technical_codes_protected():
    from action_tracker.localization.qa import audit_translation

    source = SourceFacts.from_record({"sku": "TECH-CODE", "details_es": "Material: HSS; conexión USB"})
    findings = audit_translation(source, {"details": "材质：高速钢；连接方式：有线"}, ("details",))
    assert any(item.rule_id == "PROTECTED_TOKEN_MISSING" and item.evidence.get("value") == "HSS" for item in findings)


def test_fact_qa_accepts_sock_bedding_and_footwear_scoped_equivalents():
    from action_tracker.localization.qa import audit_translation

    source = SourceFacts.from_record({
        "sku": "FAMILY-EQUIV",
        "name_es": "Calcetines",
        "desc_es": "Disponibles en los números 39-42; edredón para 2 personas",
        "details_es": "Talla de calzado: 39-42",
    })
    findings = audit_translation(
        source,
        {
            "name": "长袜",
            "description": "有39、40、41、42码可选；适合双人被子使用",
            "details": "鞋码：39,40,41,42",
        },
        ("name", "description", "details"),
    )
    assert not any(item.rule_id in {"SEMANTIC_FACT_DROPPED", "NUMERIC_ADDED"} for item in findings)


def test_source_bound_mixed_case_and_brand_tokens_are_not_spanish_residual():
    from action_tracker.localization.qa import audit_translation

    source = SourceFacts.from_record({
        "sku": "2553790",
        "name_es": "C&C cincha",
        "cat1_es": "Bricolaje",
        "cat2_es": "Coche",
        "spec_es": "1000 daN",
        "desc_es": "Con gancho Alison & Mae; 1000 daN; compatible con PlayStation; 10/100/1000 mbps",
        "details_es": "Compatible con PlayStation",
    })
    findings = audit_translation(
        source,
        {"description": "带 daN Alison & Mae PlayStation；10/100/1000 Mbps"},
        ("description",),
    )
    assert not any(item.rule_id == "SPANISH_RESIDUAL" for item in findings)


def test_sentence_leading_spanish_is_still_residual():
    from action_tracker.localization.qa import audit_translation

    source = SourceFacts.from_record({
        "sku": "2553791",
        "name_es": "Producto",
        "cat1_es": "Bricolaje",
        "cat2_es": "Coche",
        "desc_es": "Para uso general",
    })
    findings = audit_translation(source, {"description": "Para uso general"}, ("description",))
    assert any(item.rule_id == "SPANISH_RESIDUAL" for item in findings)


def test_description_repair_preserves_short_source_models():
    from action_tracker.exporting.repair_rules import repair_content_with_context

    repaired = repair_content_with_context(
        "适用于切割垫", "Alfombrilla de corte A4 para manualidades"
    )
    assert "A4" in repaired


def test_fact_qa_accepts_brand_embedded_digits_after_no_brand_cleanup():
    from action_tracker.localization.qa import audit_translation

    source = SourceFacts.from_record({"sku": "BRAND-DIGIT", "name_es": "Comba Lab31"})
    findings = audit_translation(source, {"name": "跳绳"}, ("name",))
    assert not any(item.rule_id == "NUMERIC_DROPPED" for item in findings)


def test_fact_qa_keeps_real_compact_model_digit_blocking():
    from action_tracker.localization.qa import audit_translation

    source = SourceFacts.from_record({"sku": "MODEL-DIGIT", "name_es": "Marco A4"})
    findings = audit_translation(source, {"name": "相框"}, ("name",))
    assert any(item.rule_id == "NUMERIC_DROPPED" for item in findings)


def test_fact_qa_accepts_zero_percent_as_no_content_and_one_size_as_uniform_size():
    from action_tracker.localization.qa import audit_translation

    source = SourceFacts.from_record({
        "sku": "SEMANTIC-NUMERIC",
        "desc_es": "Con un 0% de alcohol; 1 size para todos",
    })
    findings = audit_translation(
        source, {"description": "不含酒精；均码，适合所有人"}, ("description",)
    )
    assert not any(item.rule_id in {"NUMERIC_DROPPED", "UNIT_DROPPED"} for item in findings)
