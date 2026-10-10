"""Rinse truth and counted generic UV terms from independently read sources."""
import json
from pathlib import Path
import pytest
from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.qa import guard_translation

ROWS = json.loads((Path(__file__).parent / 'fixtures/historical_details13_rinse_uv_20261010.json').read_text('utf8'))

def check(source, target, description=''):
    return guard_translation(SourceFacts(sku='1001', details_es=source, desc_es=description), {'details': target}, ('details',))

@pytest.mark.parametrize('row', ROWS, ids=lambda r:r['sku'])
def test_real_source_supported_target(row):
    assert row['source_evidence']['selected']['text'] == row['source']
    assert len(row['source_evidence']['selected']['file_hash']) == 64
    assert row['formal_approval'] is False
    assert check(row['source'], row['target'])['status'] == 'PASS'

def test_real_leave_in_candidate_is_now_blocked():
    result = check(ROWS[0]['source'], ROWS[0]['candidate'])
    assert any(f['rule_id']=='DETAIL_BOOLEAN_POLARITY_CHANGED' for f in result['findings'])

@pytest.mark.parametrize('value', ['Sí', 'No'])
def test_rinse_and_leave_in_labels_have_opposite_boolean_truth(value):
    source = str({'Aclarado':value})
    positive = '是' if value=='Sí' else '否'
    inverse = '否' if value=='Sí' else '是'
    assert check(source, str({'是否需冲洗':positive}))['status']=='PASS'
    assert check(source, str({'是否免洗':inverse}))['status']=='PASS'
    assert check(source, str({'是否免洗':positive}))['status']=='FAIL'

def test_missing_rinse_attribute_stays_blocked():
    assert check("{'Aclarado':'No'}", "{'商品编号':'1001'}")['status']=='FAIL'

def test_other_field_cannot_reverse_rinse_fact():
    assert check("{'Aclarado':'No'}", "{'是否免洗':'否'}", 'Aclarado') ['status']=='FAIL'

@pytest.mark.parametrize('token', ['UVA','UVB','UVC','UV-A','UVX'])
def test_uv_alias_does_not_waive_wavelength_or_model(token):
    assert check('Lámpara '+token, '紫外线灯')['status']=='FAIL'

def test_uv_repeat_count_cannot_be_dropped_or_invented():
    source = "{'Incluye lámpara UV':'No','Requiere lámpara UV':'Sí'}"
    assert check(source, "{'是否含紫外线灯':'否','是否需紫外线灯':'是'}")['status']=='PASS'
    assert check(source, "{'是否含UV（紫外线）灯':'否','是否需灯':'是'}")['status']=='FAIL'
    assert check(source, '紫外线灯、紫外线灯、紫外线灯')['status']=='FAIL'

def test_another_field_uv_cannot_authorize_own_field_invention():
    assert check('Lámpara', 'UV灯', 'Lámpara UV')['status']=='FAIL'
