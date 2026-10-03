from __future__ import annotations

import csv
import json
from pathlib import Path

from action_tracker.localization.contracts import CANONICAL_AI_FIELDS, SourceFacts
from action_tracker.localization.resolver import Resolution
from action_tracker.localization.retranslation import build_retranslation_batch


class _FakeResolver:
    registry = None
    db_path = None
    engine = None

    def resolve(self, record, *, allow_provider=False):
        source = SourceFacts.from_record(record)
        values = {
            "name": "新商品",
            "cat1": "家居布置",
            "cat2": "清洁用品",
            "spec": "50×60cm",
            "description": "吸收污垢。",
            "details": "材质：聚酯纤维；商品编号：123456",
        }
        return {
            field: Resolution(
                source.sku,
                field,
                getattr(source, {"name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es", "spec": "spec_es", "description": "desc_es", "details": "details_es"}[field]),
                source.source_hash,
                values[field],
                "deterministic",
                "APPROVED",
                True,
                False,
                (),
                {"family_policy_version": "TEST"},
            )
            for field in CANONICAL_AI_FIELDS
        }


def test_retranslation_batch_is_read_only_and_keeps_master_baseline(tmp_path: Path):
    record = {
        "sku": "123456",
        "status": "ACTIVE",
        "name_es": "Producto nuevo",
        "cat1_es": "Hogar",
        "cat2_es": "Limpieza",
        "spec_es": "50x60 cm",
        "desc_es": "Absorbe la suciedad.",
        "details_es": "Material: Poliéster; Número del artículo: 123456",
        "name_zh": "旧中文",
        "cat1_zh": "家居布置",
        "cat2_zh": "清洁用品",
        "spec_zh": "50×60cm",
        "desc_zh": "旧描述",
        "details_zh": "旧详情",
    }
    result = build_retranslation_batch([record], resolver=_FakeResolver(), output_dir=tmp_path, limit=10)
    assert result["sku_count"] == 1
    assert result["translation_unit_count"] == 6
    assert result["production_writes"] is False
    assert result["master_modified"] is False
    rows = list(csv.DictReader((tmp_path / "retranslation_candidates.csv").open(encoding="utf-8-sig", newline="")))
    name = next(row for row in rows if row["field_name"] == "name")
    assert name["old_zh"] == "旧中文"
    assert name["candidate_zh"] == "新商品"
    assert name["resolution_source"] == "deterministic"
    assert name["owner_decision"] == ""
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "RETRANSLATION_BATCH_V1"
