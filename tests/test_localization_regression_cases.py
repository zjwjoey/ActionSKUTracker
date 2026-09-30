from pathlib import Path

from action_tracker.translation.regression_cases import (
    find_same_source_occurrences,
    load_regression_cases,
    summarize_occurrences,
)


def test_versioned_regression_corpus_is_valid_and_owner_confirmed():
    cases = load_regression_cases(Path("data/qa/localization_regressions_v1.jsonl"))
    assert len(cases) >= 14
    assert all(case.evidence_status == "OWNER_CONFIRMED" for case in cases)
    assert len({case.case_id for case in cases}) == len(cases)


def test_regression_occurrence_audit_is_source_field_bound():
    cases = load_regression_cases(Path("data/qa/localization_regressions_v1.jsonl"))
    case = next(item for item in cases if item.case_id == "DETAIL_CLEANING_TURNS_001")
    rows = [
        {"sku": "1", "details_es": "Número de turnos de limpieza: 42", "details_zh": "清洁档位数量：42"},
        {"sku": "2", "details_es": "Número de turnos de limpieza: 42", "details_zh": "洗涤次数：42"},
        {"sku": "3", "name_es": "Número de turnos de limpieza: 42", "name_zh": "错误"},
    ]
    matches = find_same_source_occurrences(rows, case)
    assert [row["sku"] for row in matches] == ["1", "2"]
    summary = summarize_occurrences(rows, [case])[0]
    assert summary["occurrence_count"] == 2
    assert summary["target_variant_count"] == 2
