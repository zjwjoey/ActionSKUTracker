"""Source-fact preservation in the native resolver, distinct from title policy."""
import json
from pathlib import Path

import pytest

from action_tracker.localization.engine import LocalizationEngine
from action_tracker.localization.providers.base import FakeTranslationProvider
from action_tracker.localization.resolver import TranslationResolver

CASE = json.loads((Path(__file__).parent / 'fixtures' /
                   'historical_pending3196_source_faithful_brands_20261011.json').read_text(encoding='utf-8'))


class CaptureProvider(FakeTranslationProvider):
    def translate(self, request):
        self.request = request
        return super().translate(request)


def brand_knowledge(*brands):
    # Approved typed terminology can identify a brand in any own field.
    return {'terms': [{'term_es': b, 'term_zh': b, 'term_type': 'BRAND'} for b in brands]}


def test_real_pencil_case_description_does_not_delete_its_source_ip_list():
    # Real SKU 3217243 own-field source fragment. The fixture output below
    # tests token policy only, not a gold translation of its full description.
    source = CASE['fragment']
    assert source in CASE['source'] == CASE['evidence']['text']
    assert len(CASE['evidence']['file_hash']) == 64
    candidate = '可选择Rey León、Frozen和Spidey等款式。'
    provider = CaptureProvider({'description': candidate})
    resolver = TranslationResolver(provider=provider, engine=LocalizationEngine(
        knowledge=brand_knowledge('Rey León', 'Frozen', 'Spidey')))
    result = resolver.resolve_field({'sku': '3217243', 'desc_es': source},
                                    'description', allow_provider=True)
    assert result.value == candidate
    assert not result.approved
    assert result.provenance['provider_raw_value'] == ''
    assert all(not term.get('display_omittable') for term in result.provenance['terminology'])


@pytest.mark.parametrize('field,source_key,source,candidate', [
    # Explicit isolated scenarios for the other field contracts.
    ('details', 'details_es', 'Marca: Spectrum; Modelo: A4', '品牌：Spectrum；型号：A4'),
    ('spec', 'spec_es', 'Spectrum | A4', 'Spectrum｜A4'),
])
def test_non_name_fields_keep_own_source_brand_and_model(field, source_key, source, candidate):
    provider = CaptureProvider({field: candidate})
    resolver = TranslationResolver(provider=provider, engine=LocalizationEngine(
        knowledge=brand_knowledge('Spectrum')))
    result = resolver.resolve_field({'sku': 'isolated-policy-scenario', source_key: source},
                                    field, allow_provider=True)
    assert result.value == candidate
    assert 'A4' in result.value
    if field == 'spec':
        # This simple measurement/model string follows native planning,
        # before the provider path. It must preserve the same source facts.
        assert result.source == 'deterministic'
        assert not hasattr(provider, 'request')
    else:
        assert all(not term.get('display_omittable') for term in result.provenance['terminology'])


def test_name_still_applies_its_no_brand_policy():
    provider = CaptureProvider({'name': 'Spectrum小型涂料滚筒'})
    resolver = TranslationResolver(provider=provider, engine=LocalizationEngine(
        knowledge={'brands': {'Spectrum'}}))
    result = resolver.resolve_field({'sku': 'isolated-title-scenario', 'name_es': 'Rodillos de pintura pequeños Spectrum'},
                                    'name', allow_provider=True)
    assert result.value == '小型涂料滚筒'
    assert result.provenance['provider_raw_value'] == 'Spectrum小型涂料滚筒'
    assert any(term.get('display_omittable') for term in result.provenance['terminology'])


def test_brand_in_title_does_not_authorize_importing_it_into_description():
    provider = CaptureProvider({'description': '柔软的面料'})
    resolver = TranslationResolver(provider=provider, engine=LocalizationEngine(
        knowledge={'brands': {'Spectrum'}}))
    result = resolver.resolve_field({'sku': 'isolated-cross-field-scenario',
                                    'name_es': 'Spectrum', 'desc_es': 'Tejido suave'},
                                    'description', allow_provider=True)
    assert result.value == '柔软的面料'
    assert not any(term.get('source') == 'Spectrum' for term in provider.request.terms)
