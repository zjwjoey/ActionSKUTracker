"""CI_SAFE: synthetic image and workbook entirely under pytest tmp_path."""
import openpyxl
from PIL import Image

from action_tracker.exporting.excel_writer import write_catalog_xlsx


def test_embedded_250px_image_column_does_not_cover_sku_and_name(tmp_path):
    image_dir = tmp_path / "excel_250"
    image_dir.mkdir()
    Image.new("RGB", (250, 250), "white").save(image_dir / "1001.png")
    headers = ["图片", "编号", "标题"]
    row = {"编号": "1001", "标题": "测试商品"}
    for embedded in (False, True):
        path = tmp_path / f"catalog_{embedded}.xlsx"
        write_catalog_xlsx(path, headers=headers, rows=[row], workbook_format={},
                           image_root=image_dir, embed_images=embedded,
                           image_eligibility={"1001": True})
        workbook = openpyxl.load_workbook(path)
        sheet = workbook.active
        assert sheet.column_dimensions["A"].width == (36 if embedded else 12)
        assert sheet["B2"].value == "1001" and sheet["C2"].value == "测试商品"
        assert len(sheet._images) == int(embedded)
        if embedded:
            assert sheet.row_dimensions[2].height >= 190
        workbook.close()
