from __future__ import annotations

from pathlib import Path

import yaml

from action_tracker.config import load_settings
from action_tracker.localization.contracts import source_hash
from action_tracker.localization.registry.repository import LocalizationRegistry
from action_tracker.localization.resolver import TranslationResolver


def _record(*, name: str = "Producto desconocido", spec: str = "medida rara", desc: str = "texto raro", details: str = "Dato raro") -> dict[str, str]:
    return {
        "sku": "safety-1",
        "name_es": name,
        "cat1_es": "Hogar",
        "cat2_es": "Otros",
        "spec_es": spec,
        "desc_es": desc,
        "details_es": details,
    }


def test_real_default_config_is_fail_closed():
    cfg = load_settings()
    assert cfg["knowledge"]["production_apply_enabled"] is False
    assert cfg["localization"]["production_apply_enabled"] is False
    assert cfg["localization"]["auto_approval_enabled"] is False
    assert cfg["localization"]["ai"]["enabled"] is False
    assert cfg["knowledge"]["fallback_to_spanish"] is False


def test_missing_safety_switches_are_normalized_closed(tmp_path: Path):
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump({"paths": {}, "knowledge": {}, "localization": {}}), encoding="utf-8")
    cfg = load_settings(path)
    assert cfg["knowledge"]["production_apply_enabled"] is False
    assert cfg["knowledge"]["fallback_to_spanish"] is False
    assert cfg["localization"]["production_apply_enabled"] is False
    assert cfg["localization"]["auto_approval_enabled"] is False
    assert cfg["localization"]["ai"]["enabled"] is False


def test_missing_approved_translation_is_pending_and_never_spanish():
    record = _record()
    resolver = TranslationResolver()
    for field, source_field in (("name", "name_es"), ("spec", "spec_es"), ("description", "desc_es"), ("details", "details_es")):
        result = resolver.resolve_field(record, field)
        assert result.status == "PENDING"
        assert result.source == "missing"
        assert result.value != record[source_field]
        assert "NO_APPROVED_RESOLUTION" in result.review_reasons


def test_approved_translation_is_returned_for_current_source(tmp_path: Path):
    record = _record(name="Producto aprobado")
    registry = LocalizationRegistry(tmp_path / "approved.sqlite")
    from action_tracker.database.connection import connect
    with connect(registry.path) as db:
        db.execute("INSERT INTO products(canonical_id,official_sku,status) VALUES(?,?,?)", ("c-safety-1", "safety-1", "ACTIVE"))
    registry.register_source("safety-1", {"name_es": record["name_es"]}, source_hash(record), observed_at="2026-10-03")
    with connect(registry.path) as db:
        unit_id = db.execute("SELECT unit_id FROM translation_units WHERE field_name='name_es'").fetchone()[0]
    revision_id = registry.record_revision(
        unit_id=str(unit_id), target_text="已批准商品", provider="fixture", model="fixture",
        request_hash="request", response_hash="response", source_hash=source_hash(record),
        qa_status="PASS", canonical_qa_status="PASS", review_status="HUMAN_REVIEWED",
    )
    assert registry.approve_revision(revision_id, actor="human:safety-test") is True
    result = TranslationResolver(db_path=registry.path, registry=registry).resolve_field(record, "name")
    assert result.value == "已批准商品"
    assert result.status == "APPROVED"
    assert result.source == "approved_revision"


def test_stale_source_hash_is_pending_and_not_spanish(tmp_path: Path):
    old = _record(name="Old product")
    current = _record(name="New product")
    registry = LocalizationRegistry(tmp_path / "stale.sqlite")
    from action_tracker.database.connection import connect
    with connect(registry.path) as db:
        db.execute("INSERT INTO products(canonical_id,official_sku,status) VALUES(?,?,?)", ("c-safety-1", "safety-1", "ACTIVE"))
    registry.register_source("safety-1", {"name_es": old["name_es"]}, source_hash(old), observed_at="2026-10-02")
    with connect(registry.path) as db:
        unit_id = db.execute("SELECT unit_id FROM translation_units WHERE field_name='name_es'").fetchone()[0]
    revision_id = registry.record_revision(
        unit_id=str(unit_id), target_text="Old Chinese", provider="fixture", model="fixture",
        request_hash="request", response_hash="response", source_hash=source_hash(old),
        qa_status="PASS", canonical_qa_status="PASS", review_status="HUMAN_REVIEWED",
    )
    assert registry.approve_revision(revision_id, actor="human:safety-test") is True
    registry.register_source("safety-1", {"name_es": current["name_es"]}, source_hash(current), observed_at="2026-10-03")
    result = TranslationResolver(db_path=registry.path, registry=registry).resolve_field(current, "name")
    assert result.status == "PENDING"
    assert result.value != current["name_es"]
    assert "NO_APPROVED_RESOLUTION" in result.review_reasons
