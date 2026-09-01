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
