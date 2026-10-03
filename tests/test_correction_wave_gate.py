from action_tracker.localization.correction_wave_gate import apply_deterministic_repairs, scan_wave
from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.semantic import parse_semantic_facts


def _rows(sku, name, cat1="", cat2="", spec="", desc="", details="", candidates=None, resolution="qwen_mt"):
    candidates = candidates or {}
    source = {"name": name, "cat1": cat1, "cat2": cat2, "spec": spec, "description": desc, "details": details}
    return [{
        "wave_id": "test", "sku": sku, "field_name": field,
        "source_es": source[field], "candidate_zh": candidates.get(field, ""),
        "resolution_source": resolution, "fact_qa_status": "PASS", "canonical_qa_status": "PASS",
    } for field in ("name", "cat1", "cat2", "spec", "description", "details")]


def test_approved_reuse_conflict_is_p0():
    rows = _rows("2506494", "Toallitas de bebé Teddy Care", cat1="Cuidado personal", cat2="Cuidados para el bebé", candidates={"name": "清洁布"}, resolution="approved_revision")
    findings, summary = scan_wave(rows)
    assert any(item["gate_rule_id"] == "APPROVED_REUSE_SEMANTIC_CONFLICT" for item in findings)
    assert summary["status"] == "FAIL"


def test_known_regressions_and_repairs_are_detected_then_cleared():
    rows = []
    rows += _rows("2509562", "Paño de microfibra", cat1="Hogar", cat2="Artículos de limpieza", details="Instrucciones de planchado: Sin planchado", candidates={"details": "熨烫说明：无需熨烫"})
    rows += _rows("2507094", "Lienzos", cat1="Hobby", cat2="Pintura", details="Tipo de paño / panel provisto de imprimación: Lienzo tensado", candidates={"cat2": "油漆", "details": "附带的清洁布/底板类型：绷紧画布"})
    findings, _ = scan_wave(rows)
    assert any(item["gate_rule_id"] == "KNOWN_REGRESSION_RECURRENCE" for item in findings)
    repaired = apply_deterministic_repairs(rows)
    findings_after, summary_after = scan_wave(repaired)
    assert not findings_after
    assert summary_after["status"] == "PASS"


def test_spec_pollution_and_duplicate_fact_are_hard_findings():
    rows = _rows("2509140", "Pastillas", spec="400 gramos", details="Carbohidratos: 98.2 g; Contenido: 400 g", candidates={"spec": "400g｜98.2g"})
    rows += _rows("2508891", "Bloc", spec="4x 100 hojas", details="Número de hojas: 400", candidates={"details": "纸张数量：400张；纸张数量：400张"})
    findings, summary = scan_wave(rows)
    assert any(item["gate_rule_id"] == "SPEC_FACT_POLLUTION" for item in findings)
    assert any(item["gate_rule_id"] == "DUPLICATED_DETAIL_FACT" for item in findings)
    assert summary["status"] == "FAIL"


def test_empty_source_empty_target_is_not_a_gate_error():
    rows = _rows("EMPTY", "Producto", candidates={"description": ""})
    findings, summary = scan_wave(rows)
    assert not any(item["gate_rule_id"] == "EMPTY_SOURCE_HALLUCINATION" for item in findings)
    assert summary["status"] == "PASS"


def test_gate_repairs_cross_field_spec_pollution_and_brand_alias():
    rows = _rows("2506494", "Toallitas de bebé Teddy Care", spec="90 unidades", desc="Toallitas Teddy Care", candidates={"name": "清洁布", "description": "泰迪呵护婴儿湿巾"}, resolution="approved_revision")
    rows += _rows("2501766", "Producto", spec="76x191x25 cm", details="Peso: 136 kg", candidates={"spec": "76×191×25cm｜136kg"})
    findings, _ = scan_wave(rows)
    assert any(item["gate_rule_id"] == "SPEC_FACT_POLLUTION" for item in findings)
    repaired = apply_deterministic_repairs(rows)
    findings_after, summary_after = scan_wave(repaired)
    assert not findings_after
    assert summary_after["status"] == "PASS"


def test_canvas_panel_pano_is_not_a_cleaning_cloth_product_fact():
    source = SourceFacts.from_record({
        "sku": "CANVAS-PANO",
        "name_es": "Lienzos Van Bleiswijck",
        "details_es": "Tipo de paño / panel provisto de imprimación: Lienzo tensado",
    })
    facts = parse_semantic_facts(source)
    assert not any(f.source_text.casefold() in {"paño", "paños"} and f.semantic_type == "PRODUCT_TYPE" for f in facts)
