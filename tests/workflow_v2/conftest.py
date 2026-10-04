from pathlib import Path

import pytest

from action_tracker.localization.providers.base import FakeTranslationProvider


class CountingFakeProvider(FakeTranslationProvider):
    def __init__(self, **kwargs):
        super().__init__(**kwargs); self.calls = 0

    def translate(self, request):
        self.calls += 1
        return super().translate(request)


@pytest.fixture
def source_row():
    return {
        "sku": "100", "canonical_id": "ACT0000100", "name_es": "Mesa", "cat1_es": "Hogar",
        "cat2_es": "Muebles", "spec_es": "10 cm", "desc_es": "Mesa roja", "details_es": "Color: Rojo; Número del artículo: 100",
        "product_url": "https://www.action.com/es-es/p/100", "current_price": 4.99, "original_price": 5.99,
        "unit_price": "4.99", "status": "CURRENT", "presence_source": "sitemap", "promotion_active": False, "action_new_badge": False,
    }


@pytest.fixture
def fake_provider():
    return CountingFakeProvider(mapping={"name": "桌子", "cat1": "家居布置", "cat2": "家具", "spec": "10 厘米", "description": "红色桌子", "details": "颜色：红色；商品编号：100"})


@pytest.fixture
def workflow_root(tmp_path):
    return tmp_path / "workflow_v2"
