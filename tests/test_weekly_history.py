"""CI_SAFE: only temporary full catalogs; no network or production state."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import openpyxl
import pytest
import yaml

from action_tracker.exporting.history import HistoryExportError, build_presence_rows, load_presence_history
from action_tracker.exporting.history_export import export_history, verify_history_xlsx


def catalog(path, skus):
    path.parent.mkdir(parents=True, exist_ok=True)
    w = openpyxl.Workbook()
    s = w.active
    s.title = "商品全量"
    s.append(["编号", "标题", "图片链接", "商品链接"])
    for sku in skus:
        s.append([sku, f"商品{sku}", f"https://images.example/{sku}.jpg", f"https://www.action.com/es-es/p/{sku}/"])
    w.save(path)
    w.close()


def setup(tmp_path):
    sources = []
    for date, skus in [("2026-11-02", ["1", "2", "2557704"]), ("2026-11-09", ["2", "3", "2557704"])]:
        p = tmp_path / f"raw_{date}.xlsx"
        catalog(p, skus)
        sources.append(dict(date=date, path=str(p), sheet="商品全量", presence_capability=True,
                            absence_capability=True, observation_complete=True,
                            expected_sku_count=len(skus), sha256=hashlib.sha256(p.read_bytes()).hexdigest()))
    raw = dict(mode="weekly_raw_catalog_v2", profile_version=2, excluded_skus=["2557704"], sources=sources)
    cfg_path = tmp_path / "sources.yaml"
    cfg_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    cfg = dict(project_root=tmp_path, history_sources_path=cfg_path,
               paths=dict(exports=tmp_path / "exports", history_archive=tmp_path / "archive", state=tmp_path / "state"))
    return cfg, raw


def save(cfg, raw):
    cfg["history_sources_path"].write_text(yaml.safe_dump(raw), encoding="utf-8")


def test_weekly_export_exact_membership_no_unknown_or_archived_extra_dates(tmp_path):
    cfg, raw = setup(tmp_path)
    # Legacy seed/archive must neither be read nor add arbitrary dates.
    raw["seed"] = {"path": str(tmp_path / "nonexistent_seed.xlsx")}
    save(cfg, raw)
    archive = cfg["paths"]["history_archive"]
    archive.mkdir()
    (archive / "legacy.xlsx").write_bytes(b"not a valid workbook")
    h = load_presence_history(cfg, as_of_date="2026-11-09")
    assert h.dates == ("2026-11-02", "2026-11-09")
    assert h.presence_by_sku == {"1": {"2026-11-02": 1, "2026-11-09": 0},
                                  "2": {"2026-11-02": 1, "2026-11-09": 1},
                                  "3": {"2026-11-02": 0, "2026-11-09": 1}}
    result = export_history(cfg, export_date="2026-11-09")
    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    assert manifest["template_version"] == 4
    assert manifest["unknown_presence_count"] == 0
    assert manifest["excluded_skus"] == ["2557704"]
    assert all(sha["status"].startswith("COMPLETE_RAW_CATALOG_SHA256:") for sha in manifest["source_stats"])
    assert build_presence_rows(h)[0]["中文品名"] == "商品1"


@pytest.mark.parametrize("case,code", [
    ("non_monday", "PERIOD_NOT_MONDAY"), ("duplicate_period", "PERIOD_DUPLICATE"),
    ("missing", "SOURCE_MISSING"), ("partial", "SOURCE_INCOMPLETE"),
    ("hash", "SOURCE_HASH_CHANGED"), ("count", "SOURCE_COUNT_CHANGED"),
    ("gap", "PERIOD_GAP"), ("duplicate_sku", "SKU_DUPLICATE"),
    ("empty", "SOURCE_EMPTY"), ("bad_link", "PRODUCT_LINK_SKU_MISMATCH"),
])
def test_bad_sources_fail_before_export(tmp_path, case, code):
    cfg, raw = setup(tmp_path)
    s = raw["sources"][0]
    if case == "non_monday": s["date"] = "2026-11-03"
    if case == "duplicate_period": raw["sources"].append(dict(s))
    if case == "missing": Path(s["path"]).unlink()
    if case == "partial": s["observation_complete"] = False
    if case == "hash": s["sha256"] = "0" * 64
    if case == "count": s["expected_sku_count"] = 999
    if case == "gap": raw["sources"][1]["date"] = "2026-11-16"
    if case in ("duplicate_sku", "empty", "bad_link"):
        catalog(Path(s["path"]), ["1", "1"] if case == "duplicate_sku" else ([] if case == "empty" else ["1"]))
        if case == "bad_link":
            w = openpyxl.load_workbook(s["path"])
            w.active["D2"] = "https://www.action.com/es-es/p/2/"
            w.save(s["path"]); w.close()
        s.pop("sha256"); s.pop("expected_sku_count")
    save(cfg, raw)
    with pytest.raises(HistoryExportError, match=code):
        export_history(cfg, export_date="2026-11-16" if case == "gap" else "2026-11-09")
    assert not list(cfg["paths"]["exports"].glob("*.xlsx"))


def test_only_monday_discovery_and_no_future_leak(tmp_path):
    cfg, raw = setup(tmp_path)
    root = tmp_path / "originals"
    for day in ("20261112", "20261116", "20261123"):
        p = root / day / f"{day}Action商品全量_中文版_不带图.xlsx"
        catalog(p, ["4", "2557704"])
        date = f'{day[:4]}-{day[4:6]}-{day[6:]}'
        p.with_suffix('.weekly-source.json').write_text(json.dumps(dict(
            contract='ACTION_WEEKLY_CATALOG_SOURCE_V1', date=date, observation_complete=True,
            qa_state='PASS', commit_status='FULL_COMMIT', authorized_by='human:owner',
            sha256=hashlib.sha256(p.read_bytes()).hexdigest(),expected_sku_count=2)),encoding='utf-8')
    raw["discover"] = dict(enabled=True, root=str(root), presence_capability=True,
                           absence_capability=True, observation_complete=True)
    save(cfg, raw)
    h = load_presence_history(cfg, as_of_date="2026-11-16")
    assert h.dates == ("2026-11-02", "2026-11-09", "2026-11-16")
    assert h.presence_by_sku["4"]["2026-11-02"] == 0
    assert "2557704" not in h.presence_by_sku
    assert load_presence_history(cfg, as_of_date="2026-11-09").dates == ("2026-11-02", "2026-11-09")


def test_missing_next_monday_blocks_instead_of_silent_omission(tmp_path):
    cfg, _ = setup(tmp_path)
    with pytest.raises(HistoryExportError, match="PERIOD_GAP"):
        load_presence_history(cfg, as_of_date="2026-11-16")


def test_template_builder_cannot_append_midweek_or_reintroduce_excluded_sku(tmp_path):
    cfg, _ = setup(tmp_path)
    h = load_presence_history(cfg, as_of_date="2026-11-09")
    current = [{"sku": s} for s in ("2", "3", "2557704")]
    rows = build_presence_rows(h, export_date="2026-11-09", current_records=current)
    assert {r["编号"] for r in rows} == {"1", "2", "3"}
    with pytest.raises(HistoryExportError, match="UNREGISTERED_PERIOD"):
        build_presence_rows(h, export_date="2026-11-10", current_records=current)
    with pytest.raises(HistoryExportError, match="CURRENT_SOURCE_CONFLICT"):
        build_presence_rows(h, export_date="2026-11-09", current_records=[{"sku": "2"}])


def test_binary_verification_rejects_unknown(tmp_path):
    cfg, _ = setup(tmp_path)
    result = export_history(cfg, export_date="2026-11-09")
    p = Path(result["output"])
    w = openpyxl.load_workbook(p)
    w["商品上下架明细"]["F2"] = "UNKNOWN"
    w.save(p); w.close()
    with pytest.raises(HistoryExportError, match="BAD_PRESENCE"):
        verify_history_xlsx(p, history_dates=("2026-11-02", "2026-11-09"),
                            expected_skus={"1", "2", "3"}, require_binary=True)


def test_reviewed_display_fields_do_not_change_any_presence_or_union(tmp_path):
    cfg, raw = setup(tmp_path)
    display = tmp_path / "reviewed.xlsx"
    w = openpyxl.Workbook(); s = w.active; s.title = "商品上下架明细"
    s.append(["编号", "中文品名", "图片链接", "商品链接", "26.11.02", "26.11.09"])
    s.append(["1", "已修订品名", "https://reviewed.example/1.jpg", "https://www.action.com/es-es/p/1/reviewed/", 0, 1])
    s.append(["999", "不应进入总体", "", "", 1, 1])
    w.save(display); w.close()
    raw["display_source"] = dict(path=str(display), sheet=s.title, date="2026-11-09")
    save(cfg, raw)
    h = load_presence_history(cfg, as_of_date="2026-11-09")
    assert h.presence_by_sku["1"] == {"2026-11-02": 1, "2026-11-09": 0}
    assert "999" not in h.presence_by_sku
    rows = {r["编号"]:r for r in build_presence_rows(h)}
    assert rows["1"]["中文品名"] == "已修订品名"
    assert rows["1"]["图片链接"] == "https://reviewed.example/1.jpg"


def test_future_raw_catalog_can_update_display_and_missing_display_blocks(tmp_path):
    cfg, raw = setup(tmp_path)
    raw["display_source"] = dict(path=str(tmp_path / "missing.xlsx"), sheet="x", date="2026-11-02")
    save(cfg, raw)
    with pytest.raises(HistoryExportError, match="DISPLAY_SOURCE_MISSING"):
        load_presence_history(cfg, as_of_date="2026-11-09")
    catalog(Path(raw["display_source"]["path"]), ["2"])
    raw["display_source"]["sheet"] = "商品全量"
    # This source has no 中文品名 column, so it cannot replace the current title.
    save(cfg, raw)
    h = load_presence_history(cfg, as_of_date="2026-11-09")
    assert h.latest_by_sku["2"]["name_zh"] == "商品2"


def test_template1_service_excludes_sku_from_all_three_sheets(tmp_path):
    from action_tracker.exporting.template1_service import export_template1
    from test_exporting import _cfg, _record, _run_log, _write_dictionary, _write_master, _write_snapshot

    cfg = _cfg(tmp_path)
    run_id, date = "2026-08-24_130145", "2026-08-24"
    record = _record("1001", last_seen=date)
    excluded = _record("2557704", last_seen=date)
    _write_master(cfg["paths"]["master"], [_run_log(run_id, date)], [record, excluded])
    _write_snapshot(cfg["paths"]["snapshots"], run_id, date, [record, excluded])
    _write_dictionary(cfg["paths"]["dictionary_baseline"], record)
    raw_path = tmp_path / "catalog.xlsx"
    catalog(raw_path, ["1001", "2557704"])
    source = dict(date=date, path=str(raw_path), sheet="商品全量", presence_capability=True,
                  absence_capability=True, observation_complete=True,expected_sku_count=2,
                  sha256=hashlib.sha256(raw_path.read_bytes()).hexdigest())
    raw = dict(mode="weekly_raw_catalog_v2", profile_version=2,
               excluded_skus=["2557704"], sources=[source])
    cfg["history_sources_path"] = tmp_path / "weekly.yaml"
    save(cfg, raw)
    result = export_template1(cfg, export_date=date, run_id=run_id)
    w = openpyxl.load_workbook(result["output"], read_only=True, data_only=True)
    try:
        for sheet in w:
            values = list(sheet.values)
            assert len(values) == 2
            assert str(values[1][1]) == "1001"
        assert list(w["商品上下架明细"].values)[0][-1] == "26.08.24"
    finally:
        w.close()
    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    assert manifest["template_version"] == 4
    assert manifest["current_valid_sku_count"] == manifest["presence_one_count"] == 1
