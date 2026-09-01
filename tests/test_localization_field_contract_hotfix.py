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
