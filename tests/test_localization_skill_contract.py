from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
REPO_SKILLS = ROOT / ".agents" / "skills"
SHORT_SKILL = REPO_SKILLS / "action-cn-localization-review"
DETAIL_SKILL = REPO_SKILLS / "action-cn-description-details-review"
QWEN_SKILL = REPO_SKILLS / "action-qwen-translation"
SHARED_CONTRACT = ROOT / "docs" / "knowledge" / "ACTION_LOCALIZATION_REVIEW_CONTRACT_V2.md"


def _text(path: Path) -> str:
    assert path.is_file(), f"repository skill is missing: {path}"
    return path.read_text(encoding="utf-8")


def test_skills_are_resolved_from_repository_only():
    assert REPO_SKILLS == ROOT / ".agents" / "skills"
    assert not str(REPO_SKILLS).lower().endswith(".codex\\skills")
    assert "PRIMARY SOURCE" in _text(QWEN_SKILL / "SKILL.md")


@pytest.mark.parametrize(
    "needle",
    [
        "KEEP",
        "CORRECTED",
        "REVIEW_REQUIRED",
        "NO_SOURCE",
        "risk_level",
        "source_hash",
        "candidate_hash",
        "reviewed_hash",
        "qwen_calls_for_review",
        "master_writes=0",
        "production_apply=false",
        "STALE_SOURCE",
        "fixed-seed human sample",
        "SOURCE_CONFLICT",
    ],
)
def test_shared_contract_contains_required_safety_contract(needle: str):
    assert needle in _text(SHARED_CONTRACT)


@pytest.mark.parametrize(
    "needle",
    [
        "USB-C",
        "approved",
        "breadcrumb/category evidence",
        "no-brand by default",
    ],
)
def test_short_field_skill_preserves_identity_and_category_rules(needle: str):
    assert needle in _text(SHORT_SKILL / "SKILL.md")


@pytest.mark.parametrize(
    "needle",
    [
        "duplicate keys",
        "never apply the name no-brand deletion rule",
        "NO_SOURCE",
        "qwen_calls_for_review=0",
        "master_writes=0",
    ],
)
def test_description_details_skill_keeps_its_boundary(needle: str):
    assert needle in _text(DETAIL_SKILL / "SKILL.md") or needle in _text(DETAIL_SKILL / "references" / "review_contract.md")


def test_category_dictionary_has_fixed_office_mapping():
    path = ROOT / "data" / "dictionary" / "category_dictionary.csv"
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    row = next(
        row for row in rows
        if row["cat1_es"] == "Oficina y papelería"
        and row["cat2_es"] == "Accesorios de oficina"
    )
    assert row["cat1_zh"] == "办公文具"
    assert row["cat2_zh"] == "办公配件"


def test_existing_fact_guard_accepts_usb_c_and_100w():
    sys.path.insert(0, str(ROOT / "src"))
    from action_tracker.translation.model_guard import validate_model_output

    result = validate_model_output(
        {"spec": "Cable USB-C 100W 2 m"},
        {"spec": "USB-C数据线100W 2m"},
        expected_fields=("spec",),
    )
    assert result.accepted is True


def test_existing_fact_guard_flags_dropped_100w():
    sys.path.insert(0, str(ROOT / "src"))
    from action_tracker.translation.model_guard import validate_model_output

    result = validate_model_output(
        {"spec": "Cable USB-C 100W 2 m"},
        {"spec": "USB-C数据线2m"},
        expected_fields=("spec",),
    )
    assert result.accepted is False
    assert "NUMERIC_DROPPED" in result.reasons
