import importlib.util
from pathlib import Path


def _module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "validate_stage4_remediation_owner_review.py"
    spec = importlib.util.spec_from_file_location("stage4_remediation_owner_review", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _audit() -> dict[str, str]:
    return {
        "sku": "1", "final_status": "APPROVED_MODEL_REVIEWED", "es_name": "Producto", "es_cat1": "Hogar", "es_cat2": "Sala",
        "es_spec": "1 unidad", "es_description": "Texto", "es_details": "Número del artículo: 1",
    }


def _review() -> dict[str, str]:
    return {
        "SKU": "1", "规则检查": "APPROVED_MODEL_REVIEWED", "人工决定": "ACCEPT_AS_GOLD", "审核人": "Owner", "审核日期": "2026-09-12",
        "西语品名": "Producto", "西语一级类目": "Hogar", "西语二级类目": "Sala", "西语规格": "1 unidad", "西语描述": "Texto", "西语产品详情": "Número del artículo: 1",
        "建议中文品名": "商品", "建议中文一级类目": "家居布置", "建议中文二级类目": "客厅", "建议中文规格": "1件", "建议中文描述": "文本", "建议中文产品详情": "商品编号：1",
    }


def test_owner_gold_requires_signed_source_matched_guarded_row():
    module = _module()
    status, reasons, target = module.classify_row(_review(), _audit())
    assert status == "HUMAN_CONFIRMED_GOLD"
    assert reasons == []
    assert target["name"] == "商品"

    pending = _review(); pending["审核人"] = ""
    status, reasons, target = module.classify_row(pending, _audit())
    assert status == "PENDING_OWNER"
    assert target is None

    changed = _review(); changed["西语规格"] = "2 unidades"
    status, reasons, target = module.classify_row(changed, _audit())
    assert status == "BLOCKED_SOURCE_CHANGED"
    assert target is None
