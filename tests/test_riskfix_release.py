"""CI_SAFE: pure in-memory workbooks and temporary source/config fixtures."""
import hashlib
import json
from pathlib import Path

import openpyxl
import pytest

from action_tracker.excel import writer
from action_tracker.exporting.history import HistoryExportError, load_presence_history
from action_tracker.exporting.history_publish import publish_history_pair
from test_weekly_history import setup, save, catalog


def price_workbook(current, low, high):
    w = openpyxl.Workbook(); w.remove(w.active)
    for title in ('01_SKU_ZH_CURRENT', '02_SKU_ES_CURRENT'):
        s = w.create_sheet(title); s.append(['SKU', '当前售价 (€)']); s.append(['101', current])
    s = w.create_sheet('08_LONG_TERM_MASTER')
    for _ in range(6): s.append([])
    s.append(writer.LONG_TERM_MASTER_HEADERS)
    ix = {h: i+1 for i,h in enumerate(writer.LONG_TERM_MASTER_HEADERS)}
    for k,v in {'正式SKU':'101','身份类型':'OFFICIAL_SKU','当前售价 (€)':high,'历史最低价 (€)':low,'历史最高价 (€)':high}.items():
        s.cell(8,ix[k]).value = v
    return w,s,ix


@pytest.mark.parametrize('current,low,high,expected', [(3,1,2,(1,3)),(.5,1,2,(.5,2)),(3,None,None,(3,3))])
def test_extrema_include_current_and_are_idempotent(current,low,high,expected):
    w,s,ix = price_workbook(current,low,high)
    for _ in range(2):
        writer._refresh_long_term_catalog(w)
        assert (s.cell(8,ix['历史最低价 (€)']).value,s.cell(8,ix['历史最高价 (€)']).value)==expected
    w.close()


def test_extrema_use_valid_history_and_ignore_invalid_prices():
    w,s,ix=price_workbook(3,1,2)
    h=w.create_sheet('03_PRICE_HISTORY');h.append(writer.PRICE_HISTORY_HEADERS)
    for old,new in [(.2,8),(-4,0),(float('nan'),float('inf'))]:
        h.append(['ACT101','101',None,old,new])
    writer._refresh_long_term_catalog(w)
    assert s.cell(8,ix['历史最低价 (€)']).value==.2
    assert s.cell(8,ix['历史最高价 (€)']).value==8
    w.close()


def test_discovery_requires_independent_receipt_and_detects_truncation(tmp_path):
    cfg,raw=setup(tmp_path)
    root=tmp_path/'originals'
    p=root/'20261116/20261116Action商品全量_中文版_不带图.xlsx'
    catalog(p,['1','2','3'])
    raw['discover']=dict(enabled=True,root=str(root),presence_capability=True,absence_capability=True,observation_complete=True)
    save(cfg,raw)
    with pytest.raises(HistoryExportError,match='COMPLETENESS_RECEIPT_MISSING'):
        load_presence_history(cfg,as_of_date='2026-11-16')
    receipt=dict(contract='ACTION_WEEKLY_CATALOG_SOURCE_V1',date='2026-11-16',authorized_by='human:owner',
                 observation_complete=True,qa_state='PASS',commit_status='FULL_COMMIT',expected_sku_count=3,
                 sha256=hashlib.sha256(p.read_bytes()).hexdigest())
    p.with_suffix('.weekly-source.json').write_text(json.dumps(receipt),encoding='utf8')
    assert load_presence_history(cfg,as_of_date='2026-11-16').presence_by_sku['3']['2026-11-16']==1
    catalog(p,['1'])
    with pytest.raises(HistoryExportError,match='SOURCE_HASH_CHANGED'):
        load_presence_history(cfg,as_of_date='2026-11-16')


def test_failed_publication_restores_previous_files(tmp_path, monkeypatch):
    import action_tracker.exporting.history_publish as pub
    output=tmp_path/'out.xlsx';output.write_bytes(b'old workbook')
    manifest=output.with_suffix('.manifest.json');manifest.write_bytes(b'old manifest')
    preview=tmp_path/'preview.xlsx';preview.write_bytes(b'new workbook')
    install=pub._install
    def fail_manifest(src,dest):
        if Path(src).name=='new-1':raise OSError('injected manifest failure')
        return install(src,dest)
    monkeypatch.setattr(pub,'_install',fail_manifest)
    with pytest.raises(OSError,match='injected'):
        publish_history_pair(preview,output,{'output_sha256':hashlib.sha256(preview.read_bytes()).hexdigest()})
    assert output.read_bytes()==b'old workbook'
    assert manifest.read_bytes()==b'old manifest'
    assert not list(tmp_path.glob('*.history-journal'))
