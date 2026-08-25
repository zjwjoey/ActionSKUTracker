"""合并历史西语 Raw 表，生成可审计的西语事实参考文件。

只复制原始表中实际存在的值，不翻译、不猜测、不修改 Master。
"""
from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

import openpyxl


HEADERS = ["sku", "date", "name_es", "spec_es", "category_es", "source_file", "source_sheet", "source_row"]
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _norm(value: object) -> str:
    return _text(value).casefold().replace(" ", "").replace("_", "")


def _column(values: list[object], patterns: tuple[str, ...]) -> int | None:
    for index, value in enumerate(values):
        normalized = _norm(value)
        if any(pattern in normalized for pattern in patterns):
            return index
    return None


def _extract_file(path: Path) -> list[dict[str, str]]:
    date = path.parent.name if re.fullmatch(r"\d{8}", path.parent.name) else ""
    rows: list[dict[str, str]] = []
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        for worksheet in workbook.worksheets:
            header: list[object] | None = None
            indexes: dict[str, int | None] = {}
            for row_number, raw_row in enumerate(worksheet.iter_rows(values_only=True), start=1):
                values = list(raw_row)
                if header is None:
                    sku = _column(values, ("productid", "编号", "sku"))
                    name = _column(values, ("nombredelproducto", "标题", "品名", "商品名称"))
                    if sku is None or name is None:
                        continue
                    header = values
                    indexes = {
                        "sku": sku,
                        "name": name,
                        "category": _column(values, ("categoria", "分类1", "一级类目", "category")),
                        "spec": _column(values, ("especificacion", "规格")),
                    }
                    continue
                sku_index = indexes["sku"]
                sku = _text(values[sku_index]) if sku_index is not None and sku_index < len(values) else ""
                if not sku:
                    continue
                extracted = {
                    "sku": sku,
                    "date": date,
                    "name_es": _text(values[indexes["name"]]) if indexes["name"] is not None and indexes["name"] < len(values) else "",
                    "spec_es": _text(values[indexes["spec"]]) if indexes["spec"] is not None and indexes["spec"] < len(values) else "",
                    "category_es": _text(values[indexes["category"]]) if indexes["category"] is not None and indexes["category"] < len(values) else "",
                    "source_file": path.name,
                    "source_sheet": worksheet.title,
                    "source_row": str(row_number),
                }
                if extracted["name_es"] or extracted["spec_es"] or extracted["category_es"]:
                    rows.append(extracted)
    finally:
        workbook.close()
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--base-reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    rows: list[dict[str, str]] = []
    if args.base_reference.exists():
        with args.base_reference.open("r", encoding="utf-8-sig", newline="") as handle:
            rows.extend({header: _text(row.get(header)) for header in HEADERS} for row in csv.DictReader(handle))
    for path in sorted(args.raw_root.glob("*/*西语版_不带图.xlsx")) + sorted(args.raw_root.glob("*/*西班牙语版_不带图.xlsx")):
        rows.extend(_extract_file(path))

    unique: dict[tuple[str, str, str, str], dict[str, str]] = {}
    for row in rows:
        key = (row["sku"], row["date"], row["source_file"], row["source_row"])
        unique[key] = row
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(unique.values())
    print({"rows": len(unique), "output": str(args.output)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
