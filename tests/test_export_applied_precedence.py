"""CI_SAFE: pure projection + temporary CSV; no DB, provider, or network.

Public source excerpts come from the 2026-10-26 audit (3207419, 3207984,
3213063). Approval metadata below is synthetic; fixtures confer no approval.
"""
import csv
from pathlib import Path

import pytest

from action_tracker.dictionary import normalize_category_key
from action_tracker.exporting.dictionary_join import (
    DictionaryContext, build_zh_rows_from_localized_source,
)
from action_tracker.exporting.repair_overrides import HEADERS, field_source_hash
from action_tracker.exporting.repair_report import ExportRepairReport, audit_repaired_rows
from action_tracker.services.hashing import localization_field_source_hash, localization_source_hash


FIELDS = {
    "name": ("name_zh", "name_es", "标题"), "cat1": ("cat1_zh", "cat1_es", "分类1"),
    "cat2": ("cat2_zh", "cat2_es", "分类2"), "spec": ("spec_zh", "spec_es", "规格"),
    "description": ("desc_zh", "desc_es", "描述"), "details": ("details_zh", "details_es", "产品详情"),
}


def _record():
    return {
        "sku": "3207419", "name_es": "Pantuflas de fieltro", "name_zh": "毛毡拖鞋",
        "cat1_es": "Moda", "cat1_zh": "服饰鞋包", "cat2_es": "Zapatos", "cat2_zh": "鞋靴",
        "spec_es": "Números 37-41 | varios colores", "spec_zh": "尺码37–41｜多种颜色",
        "desc_es": "Con suela blanca resistente\nEstas pantuflas de fieltro mantendrán tus pies calentitos. Se ponen y se quitan fácilmente y el interior está forrado con piel sintética.",
        "desc_zh": "配有结实的白色鞋底。这款毡制拖鞋可保持双脚温暖，穿脱方便，内里衬有仿毛皮。",
        "details_es": "Color: Beige, Rosa; Número del artículo: 3207419",
        "details_zh": "颜色：米色、粉色；商品编号：3207419",
        "current_price": 2.99, "unit_price": "2,99 €/ud.",
        "product_url": "https://www.action.com/es-es/p/3207419/pantuflas-de-fieltro/",
        "zh_field_provenance": {},
    }


def _approve(record, canonical):
    target, _, _ = FIELDS[canonical]
    record['zh_field_provenance'][canonical] = {
        'value': record[target], 'source_hash': localization_field_source_hash(record, canonical),
        'review_status': 'APPROVED', 'freshness_status': 'CURRENT',
        'approved_by': 'fixture:owner-proxy', 'applied_commit_id': 'fixture:applied',
    }


def _overrides(tmp_path, record, fields):
    path = tmp_path/'overrides.csv'
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=HEADERS)
        writer.writeheader()
        for canonical, replacement in fields.items():
            target, _, _ = FIELDS[canonical]
            writer.writerow(dict(sku=record['sku'], field=target, source_hash=field_source_hash(record, target),
                replacement=replacement, reason='legacy fixture override', rule_version='1',
                approved_by='fixture:legacy-review', approved_at='2026-10-01', status='APPROVED'))
    return path


def _render(record, path=None, context=None, with_report=True):
    report = ExportRepairReport() if with_report else None
    rows, _ = build_zh_rows_from_localized_source(
        [record], category_context=context, repair_report=report, repair_overrides_path=path)
    return rows[0], report


def _category_context(record):
    mapping = {'cat1_zh': '数码影音', 'cat2_zh': '手机耳机'}
    return DictionaryContext(
        directory=Path('.'), product_by_sku={}, manual_by_sku={}, model_by_sku={}, brand_by_id={},
        category_by_pair={(normalize_category_key(record['cat1_es']), normalize_category_key(record['cat2_es'])): mapping},
        category_by_cat1={}, terms=(), damage_by_sku={}, brand_reference_keys=frozenset(),
        unresolved_brand_ids=frozenset(), content_hash='fixture')


def test_complete_applied_description_wins_over_old_source_bound_override(tmp_path):
    record = _record()
    _approve(record, 'description')
    override = _overrides(tmp_path, record, {'description': '配有结实的白色鞋底'})
    row, report = _render(record, override)
    assert row['描述'] == record['desc_zh']
    assert not any(event.field == 'desc_zh' for event in report.events)
    # Idempotent projection never appends facts or truncates the approved text.
    assert _render(record, override)[0] == row


@pytest.mark.parametrize('status', ['APPROVED', 'VERIFIED', 'HUMAN_REVIEWED', 'HUMAN_APPROVED'])
def test_explicit_current_field_approvals_have_same_precedence(tmp_path, status):
    record = _record()
    _approve(record, 'description')
    record['zh_field_provenance']['description']['review_status'] = status
    row, _ = _render(record, _overrides(tmp_path, record, {'description':'配有结实的白色鞋底'}))
    assert row['描述'] == record['desc_zh']


@pytest.mark.parametrize('metadata_change', [
    {'value':'旧中文'}, {'source_hash':'old'}, {'review_status':'PENDING'},
    {'review_status':'REJECTED'}, {'freshness_status':'STALE'},
    {'approved_by':''}, {'applied_commit_id':''}, {'source_hash':None},
])
def test_incomplete_stale_or_different_binding_keeps_legacy_repair_path(tmp_path, metadata_change):
    record = _record()
    _approve(record, 'description')
    record['zh_field_provenance']['description'].update(metadata_change)
    row, report = _render(record, _overrides(tmp_path, record, {'description':'配有结实的白色鞋底'}))
    assert row['描述'] == '配有结实的白色鞋底'
    assert any(event.rule == 'APPROVED_EXPORT_OVERRIDE' and event.field == 'desc_zh' for event in report.events)


@pytest.mark.parametrize('provenance', [{}, 'invalid-json', '[]', {'description': []}])
def test_missing_or_malformed_field_approval_never_protects_row_approval(tmp_path, provenance):
    record = _record()
    record.update(zh_review_status='APPROVED', zh_freshness_status='CURRENT', zh_applied_commit_id='row-level')
    record['zh_field_provenance'] = provenance
    row, _ = _render(record, _overrides(tmp_path, record, {'description':'配有结实的白色鞋底'}))
    assert row['描述'] == '配有结实的白色鞋底'


def test_aggregate_source_hash_does_not_protect_field(tmp_path):
    record = _record()
    _approve(record, 'description')
    record['zh_field_provenance']['description']['source_hash'] = localization_source_hash(record)
    row, _ = _render(record, _overrides(tmp_path, record, {'description':'配有结实的白色鞋底'}))
    assert row['描述'] == '配有结实的白色鞋底'


def test_own_source_change_invalidates_precedence_but_other_field_change_does_not(tmp_path):
    record = _record()
    _approve(record, 'description')
    record['spec_es'] += ' | diferentes variantes'
    override = _overrides(tmp_path, record, {'description':'配有结实的白色鞋底'})
    assert _render(record, override)[0]['描述'] == record['desc_zh']
    record['desc_es'] += '\nDisponible en varios colores'
    override = _overrides(tmp_path, record, {'description':'新版旧覆盖'})
    assert _render(record, override)[0]['描述'] == '新版旧覆盖'


@pytest.mark.parametrize('with_report', [True, False])
def test_applied_description_not_reparsed_into_wrong_port_total(with_report):
    record = _record()
    record.update(sku='3213063',
        desc_es='Con 2 puertos de carga rápida y 2 puertos USB-C retráctiles\nCarga 4 dispositivos a la vez\nApto para el mechero del coche\nCarga tu teléfono en un abrir y cerrar de ojos con este cargador de coche. Solo tienes que insertarlo en el encendedor y empezar a cargarlo. Puedes utilizarlo con hasta 4 dispositivos a la vez.',
        desc_zh='带2个快充接口和2个可伸缩USB-C接口，可同时为4台设备充电；适用于汽车点烟器。将车载充电器插入点烟器即可开始充电，最多可同时连接4台设备。')
    _approve(record, 'description')
    assert _render(record, with_report=with_report)[0]['描述'] == record['desc_zh']


@pytest.mark.parametrize('with_report', [True, False])
def test_all_applied_text_fields_bypass_legacy_repair_not_only_overrides(tmp_path, monkeypatch, with_report):
    from action_tracker.exporting import dictionary_join as module
    record = _record()
    for canonical in ('name','spec','description','details'):
        _approve(record, canonical)
    def forbidden(*args, **kwargs):
        pytest.fail('approved applied field reached a legacy repair')
    for function in ('repair_title_with_context','repair_spec','repair_content_with_context',
                     'repair_details_with_context','repair_title','repair_description','repair_details'):
        monkeypatch.setattr(module, function, forbidden)
    row, _ = _render(record, _overrides(tmp_path, record,
        {field:'旧覆盖' for field in ('name','spec','description','details')}), with_report=with_report)
    for canonical in ('name','spec','description','details'):
        target, _, label = FIELDS[canonical]
        assert row[label] == record[target]


def test_applied_category_beats_legacy_dictionary_but_unapproved_category_still_maps():
    record = _record()
    record.update(sku='3207984', name_es='Protectores de pantalla Lab31', name_zh='屏幕保护膜',
        cat1_es='Multimedia', cat1_zh='数码影音', cat2_es='Cascos de teléfono', cat2_zh='手机壳及配件')
    context = _category_context(record)
    _approve(record, 'cat2')
    row, _ = _render(record, context=context)
    assert row['分类2'] == '手机壳及配件'
    record['zh_field_provenance']['cat2']['freshness_status'] = 'STALE'
    assert _render(record, context=context)[0]['分类2'] == '手机耳机'


def test_name_approval_does_not_protect_other_fields(tmp_path):
    record = _record()
    _approve(record, 'name')
    row, _ = _render(record, _overrides(tmp_path, record, {'name':'旧名称','description':'旧描述'}))
    assert row['标题'] == record['name_zh']
    assert row['描述'] == '旧描述'


def test_applied_title_still_removes_confirmed_product_brand_retains_platform_model():
    record = _record()
    record.update(name_es='Funda BrandX para iPhone 16', name_zh='BrandX适用于iPhone 16的手机壳')
    _approve(record, 'name')
    base = _category_context(record)
    context = DictionaryContext(**{**base.__dict__,
        'product_by_sku':{record['sku']:{'brand_id':'BrandX'}},
        'brand_by_id':{'BrandX':{'brand_id':'BrandX','canonical_name':'BrandX','confidence':'REFERENCE'}}})
    row, _ = _render(record, context=context)
    assert row['标题'] == '适用于iPhone 16的手机壳'


def test_missing_own_source_cannot_preserve_translation_even_with_metadata():
    record = _record()
    record['desc_es'] = ''
    _approve(record, 'description')
    assert _render(record)[0]['描述'] is None


def test_projection_precedence_does_not_bypass_numeric_qa():
    record = _record()
    record.update(spec_es='2 unidades', spec_zh='3件')
    _approve(record, 'spec')
    row, _ = _render(record)
    audit = audit_repaired_rows([record], [row])
    assert row['规格'] == '3件'
    assert not audit['release_ready']
    assert any(item['field']=='spec_zh' for item in audit['findings'])
