"""Embed existing local 250x250 white-background derivatives in the formal ES export."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from openpyxl import load_workbook
from PIL import Image

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))
from action_tracker.exporting.excel_writer import write_catalog_xlsx

SOURCE = Path(r"F:\ActionSKUTracker\runtime\exports\20260907Action商品全量_西班牙语版_正式研究版.xlsx")
OUTPUT_DIR = Path(r"F:\按日期整理\20260907")
OUTPUT = OUTPUT_DIR / "20260907Action商品全量_西班牙语版_带图.xlsx"
AUDIT = OUTPUT_DIR / "20260907Action商品全量_西班牙语版_带图.audit.json"
IMAGE_ROOT = PROJECT / "runtime" / "images" / "derivatives" / "excel_250"
HEADERS = ["图片", "编号", "标题", "分类1", "分类2", "规格", "折后价", "原价", "单价", "描述", "产品详情", "图片链接", "商品链接", "备注"]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    wb = load_workbook(SOURCE, read_only=True, data_only=True)
    ws = wb.active
    headers = [cell.value for cell in next(ws.iter_rows(min_row=1, max_row=1))]
    rows = []
    for source_row in ws.iter_rows(min_row=2):
        rows.append({header: source_row[i].value for i, header in enumerate(headers)})
    wb.close()
    if headers != HEADERS:
        raise RuntimeError(f"HEADER_MISMATCH: {headers!r}")
    if len(rows) != 5552:
        raise RuntimeError(f"SKU_COUNT_MISMATCH: {len(rows)}")

    image_skus = set()
    missing_images = []
    invalid_images = []
    for row in rows:
        sku = str(row["编号"]).strip()
        image_path = IMAGE_ROOT / f"{sku}.png"
        if not image_path.exists():
            missing_images.append(sku)
            continue
        try:
            with Image.open(image_path) as image:
                if image.size != (250, 250) or image.mode != "RGB":
                    invalid_images.append({"sku": sku, "size": image.size, "mode": image.mode})
                else:
                    image_skus.add(sku)
        except Exception as exc:  # pragma: no cover - defensive artifact check
            invalid_images.append({"sku": sku, "error": str(exc)})

    profile = {
        "sheet_name": "商品全量",
        "freeze_panes": "A2",
        "auto_filter": True,
        "header": {"bold": True, "fill": "1F4E78", "font_color": "FFFFFF"},
        "body": {
            "wrap_text_columns": ["标题", "分类1", "分类2", "规格", "描述", "产品详情", "备注"],
            "max_row_height": 405,
        },
        "price": {"number_format": "€#,##0.00"},
        "hyperlink_display_text": {"图片链接": "查看图片", "商品链接": "查看商品"},
    }
    stats = write_catalog_xlsx(
        OUTPUT,
        headers=HEADERS,
        rows=rows,
        workbook_format=profile,
        image_root=IMAGE_ROOT,
        embed_images=True,
        allowed_image_skus=image_skus,
    )

    # Verify all non-image values stayed identical and that the output embeds
    # exactly one derivative per SKU.
    out_wb = load_workbook(OUTPUT, read_only=False, data_only=True)
    out_ws = out_wb.active
    output_rows = list(out_ws.iter_rows(min_row=2, values_only=True))
    non_image_diffs = []
    for before, after in zip(rows, output_rows):
        for col, header in enumerate(HEADERS[1:], start=1):
            if before[header] != after[col]:
                non_image_diffs.append({"sku": str(before["编号"]), "field": header})
                break
    embedded_count = len(out_ws._images)
    out_wb.close()

    audit = {
        "source_file": str(SOURCE),
        "output_file": str(OUTPUT),
        "source_sha256": sha256(SOURCE),
        "sku_count": len(rows),
        "image_derivative_root": str(IMAGE_ROOT),
        "image_profile": "excel_250_white_v1",
        "derivative_valid_count": len(image_skus),
        "missing_image_count": len(missing_images),
        "missing_image_skus": missing_images,
        "invalid_image_count": len(invalid_images),
        "invalid_images": invalid_images,
        "embedded_image_count": embedded_count,
        "writer_stats": stats,
        "non_image_value_diff_count": len(non_image_diffs),
        "non_image_value_diffs": non_image_diffs[:50],
        "freeze_panes": out_ws.freeze_panes if hasattr(out_ws, "freeze_panes") else None,
        "auto_filter": out_ws.auto_filter.ref if hasattr(out_ws, "auto_filter") else None,
        "qc_result": "PASS" if (
            len(rows) == 5552
            and len(image_skus) == 5552
            and not missing_images
            and not invalid_images
            and embedded_count == 5552
            and not non_image_diffs
        ) else "FAIL",
    }
    AUDIT.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False))


if __name__ == "__main__":
    main()
