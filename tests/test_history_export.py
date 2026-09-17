from __future__ import annotations

import json
from pathlib import Path

import openpyxl
import pytest

from action_tracker.exporting.history import PRESENCE_UNKNOWN, filter_history_for_export, load_presence_history
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
        sheet = workbook["\u5546\u54c1\u4e0a\u4e0b\u67b6\u660e\u7ec6"]
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
    assert manifest["history_union_sku_count"] == 2
    assert manifest["history_dates"] == ["2026-08-24", "2026-08-25"]
    assert manifest["unknown_presence_count"] == 0
    assert len(manifest["seed_sha256"]) == 64


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


def _write_matrix_source(path: Path, date_header: str) -> None:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "商品上下架明细"
    sheet.append(["序号", "编号", "中文品名", "图片链接", "商品链接", date_header])
    sheet.append([1, "9001", "历史商品1", "", "https://www.action.com/es-es/p/9001/", 1])
    sheet.append([2, "9003", "新增商品3", "", "https://www.action.com/es-es/p/9003/", 1])
    sheet.append([3, "9002", "历史商品2", "", "https://www.action.com/es-es/p/9002/", 0])
    workbook.save(path)
    workbook.close()


def test_export_archive_is_reused_and_new_monday_source_is_merged(tmp_path: Path):
    seed = tmp_path / "seed.xlsx"
    _write_seed(seed)
    cfg = _history_cfg(tmp_path, seed)
    first = export_history(cfg, export_date="2026-08-30")
    assert Path(first["archive"]["path"]).exists()

    source = tmp_path / "20260831.xlsx"
    _write_matrix_source(source, "26.08.31")
    source_cfg = cfg["history_sources_path"].read_text(encoding="utf-8")
    source_cfg = source_cfg.replace(
        "sources: []",
        "sources:\n"
        "  - date: '2026-08-31'\n"
        f"    path: '{source.as_posix()}'\n"
        "    sheet: \u5546\u54c1\u4e0a\u4e0b\u67b6\u660e\u7ec6\n    sku_header: \u7f16\u53f7\n"
        "    presence_header: '26.08.31'\n"
        "    presence_capability: true\n    absence_capability: true\n"
        "    observation_complete: true\n    evidence_level: A\n    fields: {}\n",
    )
    cfg["history_sources_path"].write_text(source_cfg, encoding="utf-8")
    second = export_history(cfg, export_date="2026-09-01")
    workbook = openpyxl.load_workbook(second["output"], data_only=True)
    try:
        sheet = workbook["\u5546\u54c1\u4e0a\u4e0b\u67b6\u660e\u7ec6"]
        headers = [cell.value for cell in sheet[1]]
        assert "26.08.31" in headers
        sku_col = headers.index("\u7f16\u53f7") + 1
        date_col = headers.index("26.08.31") + 1
        values = {
            str(sheet.cell(row=row, column=sku_col).value): sheet.cell(row=row, column=date_col).value
            for row in range(2, sheet.max_row + 1)
        }
        assert values["9001"] == 1 and values["9003"] == 1 and values["9002"] == 0
    finally:
        workbook.close()


def test_export_profile_excludes_april_date_without_removing_raw_source(tmp_path: Path):
    seed = tmp_path / "seed.xlsx"
    _write_seed(seed)
    source = tmp_path / "april.xlsx"
    _write_matrix_source(source, "26.04.05")
    cfg = _history_cfg(tmp_path, seed)
    cfg["history_sources_path"].write_text(
        cfg["history_sources_path"].read_text(encoding="utf-8").replace(
            "sources: []",
            "sources:\n"
            "  - date: '2026-04-05'\n"
            f"    path: '{source.as_posix()}'\n"
            "    sheet: \u5546\u54c1\u4e0a\u4e0b\u67b6\u660e\u7ec6\n    sku_header: \u7f16\u53f7\n"
            "    presence_header: '26.04.05'\n    presence_capability: true\n"
            "    absence_capability: true\n    observation_complete: true\n"
            "    evidence_level: A\n    fields: {}",
        ),
        encoding="utf-8",
    )
    result = export_history(cfg, export_date="2026-08-30")
    workbook = openpyxl.load_workbook(result["output"], data_only=True)
    try:
        headers = [cell.value for cell in workbook["\u5546\u54c1\u4e0a\u4e0b\u67b6\u660e\u7ec6"][1]]
        assert "26.04.05" not in headers
        sku_values = [row[1].value for row in workbook["\u5546\u54c1\u4e0a\u4e0b\u67b6\u660e\u7ec6"].iter_rows(min_row=2)]
        assert "9003" not in {str(value) for value in sku_values}
    finally:
        workbook.close()


def test_export_profile_keeps_only_frozen_dates(tmp_path: Path):
    seed = tmp_path / "seed.xlsx"
    _write_seed(seed)
    cfg = _history_cfg(tmp_path, seed)
    cfg["export_history"] = {
        "include_dates": ["2026-08-24", "2026-08-26"],
        "exclude_dates": [],
    }
    history = filter_history_for_export(
        load_presence_history(cfg, as_of_date="2026-08-30"), cfg, as_of_date="2026-08-30",
    )
    assert history.dates == ("2026-08-24",)
