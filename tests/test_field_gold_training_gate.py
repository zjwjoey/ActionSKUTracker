from __future__ import annotations

import importlib.util
from pathlib import Path


def load_gate():
    path = Path(__file__).parents[1] / "scripts" / "certify_qwen_field_gold_training_gate.py"
    spec = importlib.util.spec_from_file_location("field_gold_gate", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_field_level_and_full_record_rows_are_supported():
    gate = load_gate()
    field = gate.parse_messages_row({"messages": [{"role": "user", "content": '{"name":"Producto"}'}], "metadata": {"sku": "1"}})
    assert field and field["sku"] == "1" and field["field"] == "name"
    full = gate.parse_messages_row({"messages": [{"role": "user", "content": '{"name":"Producto","spec":"1 unidad"}'}], "metadata": {"sku": "2"}})
    assert full and full["sku"] == "2" and full["field"] == ""


def test_overlap_audit_rejects_train_validation_test_and_frozen():
    gate = load_gate()
    source = {field: "" for field in gate.FIELDS}
    source["name"] = "Producto"
    gold = [{"sku": "1", "field": "name", "source": source, "metadata": {"official_source_hash": gate.source_hash(source)}}]
    registry = [
        {"sku": "1", "field": "name", "split": "TRAIN", "dataset_id": "t", "artifact_path": "t", "source_hash": "", "source_fingerprint": gate.source_fingerprint(source), "family_id": "producto", "family_source": "test"},
        {"sku": "2", "field": "name", "split": "VALIDATION", "dataset_id": "v", "artifact_path": "v", "source_hash": "", "source_fingerprint": "", "family_id": "otro", "family_source": "test"},
    ]
    audit = gate.overlap_for_gold(gold, registry, {"1"})[0]
    assert audit["historical_train_seen"]
    assert audit["frozen_test_seen"]
    assert audit["gate1_status"] == "BLOCK"


def test_group_split_is_deterministic_and_family_disjoint():
    gate = load_gate()
    rows = [{"sku": str(i), "field": "name", "family_id": f"family-{i // 2}"} for i in range(10)]
    train_a, valid_a = gate.deterministic_group_split(rows)
    train_b, valid_b = gate.deterministic_group_split(rows)
    assert [row["sku"] for row in train_a] == [row["sku"] for row in train_b]
    assert [row["sku"] for row in valid_a] == [row["sku"] for row in valid_b]
    assert {row["family_id"] for row in train_a}.isdisjoint({row["family_id"] for row in valid_a})


def test_family_ambiguity_fails_closed():
    gate = load_gate()
    family, method = gate.family_for({field: "" for field in gate.FIELDS})
    assert family == ""
    assert method == "FAMILY_AMBIGUOUS"
