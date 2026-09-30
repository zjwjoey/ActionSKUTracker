import importlib.util
from pathlib import Path


def _module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "build_stage4_remediation_candidates.py"
    spec = importlib.util.spec_from_file_location("stage4_remediation_candidates", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_remediation_candidate_requires_master_queue_source_match():
    module = _module()
    queue = {
        "sku": "100", "source_name_es": "Nombre", "source_spec_es": "10 g",
        "source_description_es": "Descripción", "source_details_es": "Detalle",
        "failure_class": "NUMERIC_MISSING", "target_field": "description", "family_proxy": "A / B",
    }
    es = {
        "SKU": "100", "西班牙语品名": "Nombre", "一级类目（西语）": "A", "二级类目（西语）": "B",
        "规格（西语）": "10 g", "描述（西语）": "Descripción", "产品详情（西语）": "Detalle",
    }
    zh = {
        "SKU": "100", "中文品名": "名称", "一级类目（中文）": "DIY五金", "二级类目（中文）": "测试",
        "规格（中文）": "10克", "中文描述": "描述", "中文产品详情": "详情",
    }
    candidate, status = module.build_candidate(queue, es, zh)
    assert status == "READY_FOR_TWO_PASS_REVIEW"
    assert candidate["metadata"]["training_eligible"] is False
    es["描述（西语）"] = "Changed"
    candidate, status = module.build_candidate(queue, es, zh)
    assert candidate is None
    assert status == "SOURCE_QUEUE_MISMATCH"
