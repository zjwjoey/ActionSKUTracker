from action_tracker.services.hashing import (
    field_source_hash,
    localization_field_source_hashes,
    localization_source_hash,
)
import pytest


def _source():
    return {
        "name_es": "Producto",
        "cat1_es": "Hogar",
        "cat2_es": "Limpieza",
        "spec_es": "2 piezas",
        "desc_es": "Para casa",
        "details_es": "Material: Plástico",
    }


def test_description_change_does_not_stale_details_or_spec():
    before = localization_field_source_hashes(_source())
    changed = {**_source(), "desc_es": "Para jardín"}
    after = localization_field_source_hashes(changed)
    assert after["description"] != before["description"]
    assert after["details"] == before["details"]
    assert after["spec"] == before["spec"]


def test_details_change_does_not_stale_description():
    before = localization_field_source_hashes(_source())
    changed = {**_source(), "details_es": "Material: Metal"}
    after = localization_field_source_hashes(changed)
    assert after["details"] != before["details"]
    assert after["description"] == before["description"]


def test_empty_source_hash_is_deterministic_and_unknown_field_fails():
    row = {"desc_es": ""}
    assert field_source_hash(row, "description") == field_source_hash(row, "description")
    try:
        field_source_hash(row, "unknown")
    except ValueError as exc:
        assert str(exc) == "UNKNOWN_LOCALIZATION_FIELD:unknown"
    else:
        raise AssertionError("unknown localization field was accepted")


def test_legacy_overall_hash_remains_distinct_from_new_field_hash():
    row = _source()
    assert localization_source_hash(row) != field_source_hash(row, "description")


def test_field_hash_never_falls_back_to_target_value():
    with pytest.raises(ValueError, match="MISSING_LOCALIZATION_SOURCE:name:name_es"):
        field_source_hash({"name": "中文商品"}, "name")
