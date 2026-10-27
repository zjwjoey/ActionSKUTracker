from __future__ import annotations

import json
from pathlib import Path

import openpyxl
import pytest

from action_tracker.exporting.history import PRESENCE_UNKNOWN, load_presence_history
from action_tracker.exporting.history_export import HistoryExportError, export_history

from test_exporting import _cfg


def _write_seed(path: Path) -> None:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "ACTION商品上下架明细"
    sheet.append(["序号", "编号", "中文品名", "图片链接", "商品链接", "26.08.24", "26.08.25"])
    sheet.append([1, "9002", "历史商品2", "https://images.example/9002.jpg", "https://www.action.com/es-es/p/9002/", 0, 1])
    sheet.append([2, "9001", "历史商品1", "", "https://www.action.com/es-es/p/9001/", 1, 0])
    workbook.save(path)
    workbook.close()


def _history_cfg(tmp_path: Path, seed: Path, *, partial: bool = False, source: Path | None = None) -> dict:
    cfg = _cfg(tmp_path)
    config = tmp_path / "history_sources.yaml"
    lines = [
        "seed:", f"  path: '{seed.as_posix()}'", "  sheet: ACTION商品上下架明细", "  sku_header: 编号",
        "  presence_capability: true", "  absence_capability: true", "  observation_complete: true", "  evidence_level: A",
        "  fields:", "    name_zh: 中文品名", "    image_url: 图片链接", "    product_url: 商品链接",
    ]
    if partial:
        lines += [
            "sources:", "  - date: '2026-08-26'", f"    path: '{(source or seed).as_posix()}'",
            "    sheet: ACTION商品上下架明细", "    sku_header: 编号", "    presence_capability: true",
            "    absence_capability: false", "    observation_complete: false", "    evidence_level: B", "    fields: {}",
        ]
    else:
        lines.append("sources: []")
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")
    cfg["history_sources_path"] = config
    return cfg


def test_export_history_writes_union_and_manifest(tmp_path):
    seed = tmp_path / "seed.xlsx"
    _write_seed(seed)
    cfg = _history_cfg(tmp_path, seed)
    result = export_history(cfg, export_date="2026-08-30")

    workbook = openpyxl.load_workbook(result["output"], data_only=True)
    try:
        assert workbook.sheetnames == ["商品上下架明细", "历史来源审计"]
        sheet = workbook["商品上下架明细"]
        headers = [cell.value for cell in sheet[1]]
        assert headers[:5] == ["序号", "编号", "中文品名", "图片链接", "商品链接"]
        assert headers[-2:] == ["26.08.24", "26.08.25"]
        date_start = headers.index("26.08.24") + 1
        rows = {str(sheet.cell(row=row, column=2).value): [sheet.cell(row=row, column=col).value for col in range(date_start, date_start + 2)] for row in range(2, sheet.max_row + 1)}
        assert rows == {"9001": [1, 0], "9002": [0, 1]}
        assert sheet.freeze_panes == "A2"
        assert sheet.auto_filter.ref == "A1:G3"
        assert workbook["历史来源审计"].max_row == 3
    finally:
        workbook.close()

    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    assert manifest["template_id"] == "action_history_presence"
    assert manifest["template_version"] == 2
    assert manifest["history_union_sku_count"] == 2
    assert manifest["history_dates"] == ["2026-08-24", "2026-08-25"]
    assert manifest["unknown_presence_count"] == 0
    assert len(manifest["seed_sha256"]) == 64


def test_unknown_seed_roundtrip_and_template1_columns_agree(tmp_path):
    from action_tracker.exporting.template1 import HISTORY_HEADERS, write_template1_xlsx
    seed = tmp_path / "seed.xlsx"
    _write_seed(seed)
    w = openpyxl.load_workbook(seed)
    w.active["F2"] = "UNKNOWN"
    w.save(seed); w.close()
    cfg = _history_cfg(tmp_path, seed)
    result = export_history(cfg, export_date="2026-08-30")
    manifest = json.loads(Path(result["manifest"]).read_text("utf8"))
    assert manifest["unknown_presence_count"] == 1
    # Feeding the module's own output back into the same reader must retain UNKNOWN.
    again_cfg = _history_cfg(tmp_path, Path(result["output"]))
    text = again_cfg["history_sources_path"].read_text("utf8").replace("sheet: ACTION商品上下架明细", "sheet: 商品上下架明细")
    again_cfg["history_sources_path"].write_text(text, "utf8")
    history = load_presence_history(again_cfg)
    assert history.presence_by_sku["9002"]["2026-08-24"] == "UNKNOWN"
    out = tmp_path / "template1.xlsx"
    write_template1_xlsx(out, history_rows=[], history_dates=history.dates, es_rows=[], zh_rows=[])
    w = openpyxl.load_workbook(out)
    assert tuple(c.value for c in w.active[1][:5]) == HISTORY_HEADERS
    assert w.active.max_column == 5 + len(history.dates)
    w.close()


def _matrix(path, rows, header="26.08.26"):
    w = openpyxl.Workbook(); w.active.title = "ACTION商品上下架明细"
    w.active.append(["编号", header])
    for row in rows: w.active.append(row)
    w.save(path); w.close()


@pytest.mark.parametrize("complete", [False, True])
def test_presence_header_preserves_zero_unknown_and_excludes_zero_only_skus(tmp_path, complete):
    seed = tmp_path / "seed.xlsx"; _write_seed(seed)
    source = tmp_path / "matrix.xlsx"
    _matrix(source, [["9001", 0], ["9002", "UNKNOWN"], ["9003", 1], ["9004", 0], ["9005", "UNKNOWN"]])
    cfg = _history_cfg(tmp_path, seed, partial=True, source=source)
    p = cfg["history_sources_path"]
    p.write_text(p.read_text("utf8").replace("    presence_capability: true", "    presence_header: '26.08.26'\n    presence_capability: true"), "utf8")
    if complete:
        p.write_text(p.read_text("utf8").replace("absence_capability: false", "absence_capability: true").replace("observation_complete: false", "observation_complete: true"), "utf8")
    history = load_presence_history(cfg)
    assert set(history.presence_by_sku) == {"9001", "9002", "9003"}
    assert history.presence_by_sku["9001"]["2026-08-26"] == 0
    assert history.presence_by_sku["9002"]["2026-08-26"] == "UNKNOWN"
    assert history.presence_by_sku["9003"]["2026-08-26"] == 1


@pytest.mark.parametrize("value", [2, -1, "", None, True, "unknown"])
def test_matrix_invalid_values_fail_closed(tmp_path, value):
    seed = tmp_path / "seed.xlsx"; _write_seed(seed)
    source = tmp_path / "matrix.xlsx"; _matrix(source, [["9001", value]])
    cfg = _history_cfg(tmp_path, seed, partial=True, source=source)
    p = cfg["history_sources_path"]
    p.write_text(p.read_text("utf8").replace("    presence_capability: true", "    presence_header: '26.08.26'\n    presence_capability: true"), "utf8")
    with pytest.raises(HistoryExportError, match="HISTORY_SOURCE_BAD_PRESENCE"):
        load_presence_history(cfg)


def test_matrix_missing_header_and_conflicting_duplicates_are_blocked(tmp_path):
    seed = tmp_path / "seed.xlsx"; _write_seed(seed)
    source = tmp_path / "matrix.xlsx"; _matrix(source, [["9001", 1]], header="wrong")
    cfg = _history_cfg(tmp_path, seed, partial=True, source=source)
    p = cfg["history_sources_path"]
    p.write_text(p.read_text("utf8").replace("    presence_capability: true", "    presence_header: '26.08.26'\n    presence_capability: true"), "utf8")
    with pytest.raises(HistoryExportError, match="HISTORY_PRESENCE_HEADER_NOT_FOUND"):
        load_presence_history(cfg)
    _matrix(source, [["9001", 1], ["9001", 0]])
    with pytest.raises(HistoryExportError, match="HISTORY_SOURCE_PRESENCE_CONFLICT"):
        load_presence_history(cfg)


def test_excluded_month_skips_source_and_april_only_seed_membership(tmp_path):
    import yaml
    seed = tmp_path / "seed.xlsx"
    w = openpyxl.Workbook(); w.active.title = "ACTION商品上下架明细"
    w.active.append(["编号", "26.04.05", "26.08.24"])
    w.active.append(["9001", 1, 0]); w.active.append(["9002", 1, 1]); w.active.append(["9003", 0, "UNKNOWN"])
    w.save(seed); w.close()
    cfg = _history_cfg(tmp_path, seed)
    p = cfg["history_sources_path"]; raw = yaml.safe_load(p.read_text("utf8"))
    raw["excluded_months"] = ["2026-04"]
    raw["sources"] = [{"date": "2026-04-12", "path": "missing-april-file.xlsx"}]
    p.write_text(yaml.safe_dump(raw), "utf8")
    h = load_presence_history(cfg)
    assert h.dates == ("2026-08-24",)
    assert set(h.presence_by_sku) == {"9002", "9003"}
    assert h.presence_by_sku["9003"]["2026-08-24"] == "UNKNOWN"
    result = export_history(cfg, export_date="2026-10-26")
    manifest = json.loads(Path(result["manifest"]).read_text("utf8"))
    assert manifest["excluded_months"] == ["2026-04"]
    assert all(not x["date"].startswith("2026-04") for x in manifest["source_stats"])


@pytest.mark.parametrize("months", ["2026-04", ["2026-13"], [4]])
def test_invalid_exclusion_config_is_blocked(tmp_path, months):
    import yaml
    seed = tmp_path / "seed.xlsx"; _write_seed(seed)
    cfg = _history_cfg(tmp_path, seed); p = cfg["history_sources_path"]
    raw = yaml.safe_load(p.read_text("utf8")); raw["excluded_months"] = months
    p.write_text(yaml.safe_dump(raw), "utf8")
    with pytest.raises(HistoryExportError, match="HISTORY_EXCLUDED_MONTHS_INVALID"):
        load_presence_history(cfg)


def test_new_current_sku_uses_historical_absence_capability_without_rewriting_unknown(tmp_path):
    from dataclasses import replace
    from action_tracker.exporting.history import build_presence_rows
    seed = tmp_path / "seed.xlsx"; _write_seed(seed)
    w = openpyxl.load_workbook(seed); w.active["F2"] = "UNKNOWN"; w.save(seed); w.close()
    h = load_presence_history(_history_cfg(tmp_path, seed))
    rows = {r["编号"]: r for r in build_presence_rows(h, export_date="2026-10-26", current_records=[{"sku": "9003"}])}
    assert rows["9003"]["2026-08-24"] == 0
    assert rows["9003"]["2026-08-25"] == 0
    assert rows["9003"]["2026-10-26"] == 1
    assert rows["9002"]["2026-08-24"] == "UNKNOWN"
    partial = replace(h, source_stats=tuple(replace(s, observation_complete=False) for s in h.source_stats))
    r = next(r for r in build_presence_rows(partial, current_records=[{"sku": "9003"}]) if r["编号"] == "9003")
    assert r["2026-08-24"] == "UNKNOWN"
    unknown_capability = replace(h, source_stats=())
    r = next(r for r in build_presence_rows(unknown_capability, current_records=[{"sku": "9003"}]) if r["编号"] == "9003")
    assert r["2026-08-24"] == "UNKNOWN"


def test_partial_source_absence_is_unknown(tmp_path):
    seed = tmp_path / "seed.xlsx"
    _write_seed(seed)
    partial = tmp_path / "partial.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "ACTION商品上下架明细"
    sheet.append(["序号", "编号", "中文品名", "图片链接", "商品链接"])
    sheet.append([1, "9002", "历史商品2", "", ""])
    workbook.save(partial)
    workbook.close()
    history = load_presence_history(_history_cfg(tmp_path, seed, partial=True, source=partial))
    assert history.presence_by_sku["9001"]["2026-08-26"] == PRESENCE_UNKNOWN


def test_export_history_rejects_invalid_presence(tmp_path):
    seed = tmp_path / "seed.xlsx"
    _write_seed(seed)
    workbook = openpyxl.load_workbook(seed)
    workbook["ACTION商品上下架明细"]["F2"] = 2
    workbook.save(seed)
    workbook.close()
    with pytest.raises(HistoryExportError, match="HISTORY_SEED_BAD_PRESENCE"):
        export_history(_history_cfg(tmp_path, seed), export_date="2026-08-30")


def test_missing_capability_config_is_rejected(tmp_path):
    seed = tmp_path / "seed.xlsx"
    _write_seed(seed)
    cfg = _history_cfg(tmp_path, seed)
    cfg["history_sources_path"].write_text(
        cfg["history_sources_path"].read_text(encoding="utf-8").replace("  evidence_level: A\n", ""),
        encoding="utf-8",
    )
    with pytest.raises(HistoryExportError, match="HISTORY_CAPABILITY_CONFIG_MISSING"):
        load_presence_history(cfg)
