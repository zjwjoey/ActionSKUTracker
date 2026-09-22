from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / ".agents" / "skills"


def _text(path: Path) -> str:
    assert path.is_file(), f"repository skill is missing: {path}"
    return path.read_text(encoding="utf-8")


def test_skills_are_resolved_from_repository_only():
    assert SKILLS == ROOT / ".agents" / "skills"
    assert ".codex" not in str(SKILLS).lower()
    assert "PRIMARY SOURCE" in _text(SKILLS / "action-qwen-translation" / "SKILL.md")


def test_shared_field_and_review_contracts_are_present():
    qwen = _text(SKILLS / "action-qwen-translation" / "SKILL.md")
    details = _text(SKILLS / "action-cn-description-details-review" / "SKILL.md")
    short = _text(SKILLS / "action-cn-localization-review" / "SKILL.md")
    for needle in ("CONTEXT FOR DISAMBIGUATION", "NO_SOURCE", "GUARD PASS IS NOT TRANSLATION PASS"):
        assert needle in qwen
    for needle in ("MODEL-LED MTPE SEMANTIC REVIEWER", "Voltaje", "Contenido", "duplicate keys"):
        assert needle in details
    for needle in ("no-brand", "fixed 15-category mapping", "USB-C", "CORRECTED"):
        assert needle in short
    for skill in (qwen, short):
        for needle in ("ordinary brand names", "model", "series", "technical tokens", "identity-bearing IP"):
            assert needle in skill


def test_regression_fixtures_include_guard_boundary_and_source_conflict():
    for name in (
        "action-qwen-translation",
        "action-cn-description-details-review",
    ):
        path = SKILLS / name / "examples" / "semantic_regressions.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        ids = {item["id"] for item in payload["cases"]}
        assert "source_conflict" in ids
        assert any(item.get("keep") == 1999 and item.get("corrected") == 0 for item in payload["cases"])
