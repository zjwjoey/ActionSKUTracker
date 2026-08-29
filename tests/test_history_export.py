from __future__ import annotations

import json
from pathlib import Path

import openpyxl
import pytest

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


def _history_cfg(tmp_path: Path, seed: Path) -> dict:
    cfg = _cfg(tmp_path)
    config = tmp_path / "history_sources.yaml"
    config.write_text(
        "seed:\n"
        f"  path: '{seed.as_posix()}'\n"
        "  sheet: ACTION商品上下架明细\n"
        "  sku_header: 编号\n"
        "  fields:\n"
        "    name_zh: 中文品名\n"
        "    image_url: 图片链接\n"
        "    product_url: 商品链接\n"
        "sources: []\n",
        encoding="utf-8",
    )
    cfg["history_sources_path"] = config
    return cfg


def test_export_history_writes_union_and_manifest(tmp_path):
    seed = tmp_path / "seed.xlsx"
    _write_seed(seed)
    cfg = _history_cfg(tmp_path, seed)
    result = export_history(cfg, export_date="2026-08-30")

    workbook = openpyxl.load_workbook(result["output"], data_only=True)
    try:
        assert workbook.sheetnames == ["商品上下架明细"]
        sheet = workbook["商品上下架明细"]
        assert [cell.value for cell in sheet[1]] == ["序号", "编号", "中文品名", "图片链接", "商品链接", "26.08.24", "26.08.25"]
        rows = {str(sheet.cell(row=row, column=2).value): [sheet.cell(row=row, column=col).value for col in range(6, 8)] for row in range(2, sheet.max_row + 1)}
        assert rows == {"9001": [1, 0], "9002": [0, 1]}
        assert sheet.freeze_panes == "A2"
        assert sheet.auto_filter.ref == "A1:G3"
    finally:
        workbook.close()

    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    assert manifest["template_id"] == "action_history_presence"
    assert manifest["history_union_sku_count"] == 2
    assert manifest["history_dates"] == ["2026-08-24", "2026-08-25"]
    assert len(manifest["seed_sha256"]) == 64


def test_export_history_rejects_invalid_presence(tmp_path):
    seed = tmp_path / "seed.xlsx"
    _write_seed(seed)
    workbook = openpyxl.load_workbook(seed)
    workbook["ACTION商品上下架明细"]["F2"] = 2
    workbook.save(seed)
    workbook.close()
    with pytest.raises(HistoryExportError, match="HISTORY_SEED_BAD_PRESENCE"):
        export_history(_history_cfg(tmp_path, seed), export_date="2026-08-30")
