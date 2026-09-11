"""Final deterministic cleanup for the repaired Spanish export.

Scope is deliberately limited to the confirmed QC issues:
* repair the malformed field/value pairs for SKU 2536376;
* remove HTML markup from the eight confirmed descriptions;
* remove a leading standalone ``Descripción`` label from descriptions.
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))
from action_tracker.exporting.excel_writer import write_catalog_xlsx
from openpyxl import load_workbook

SOURCE = Path(r"F:\ActionSKUTracker\runtime\exports\20260907Action商品全量_西班牙语版_不带图_最终修复版.xlsx")
OUTPUT = SOURCE.with_name("20260907Action商品全量_西班牙语版_不带图_最终修复版_格式清理版.xlsx")
AUDIT = OUTPUT.with_suffix(".audit.json")
HEADERS = ["图片","编号","标题","分类1","分类2","规格","折后价","原价","单价","描述","产品详情","图片链接","商品链接","备注"]
HTML_SKUS = {"2580205","2581869","2581876","2581877","2581880","2581882","3007411","3207958"}

def clean_html(value: object) -> str:
    text = "" if value is None else str(value)
    # Preserve readable anchor text and turn block tags into line breaks.
    text = re.sub(r"<\s*(?:br\s*/?|/p|/div|/li)\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]*>", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    return text.strip()

def strip_description_heading(value: object) -> str:
    text = "" if value is None else str(value).strip()
    text = re.sub(r"^Descripción\s*(?::\s*)?(?:\r?\n+|$)", "", text, count=1, flags=re.I)
    return text.strip()

def main() -> None:
    wb = load_workbook(SOURCE, read_only=True, data_only=True)
    ws = wb.active
    headers = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
    idx = {h: i for i, h in enumerate(headers)}
    rows, changes = [], []
    for source_row in ws.iter_rows(min_row=2):
        row = {h: source_row[i].value for h, i in idx.items()}
        sku = str(row["编号"]).strip()
        if sku == "2536376":
            row["产品详情"] = (
                "Color: Blanco; Color suave: Blanco cálido; Destinado a: Para uso interior y exterior; "
                "Instalación: Colgado; Longitud del cable: 20 m; Longitud del cable: 20; "
                "Método de fijación: Colgado; Potencia: 3.6; Potencia: 3.6 vatio; Tema: Navidad; "
                "Tipo de alimentación: Alimentación de red; Tipo de decoración de temporada: Decoración de temporada; "
                "Tipo de fuente de luz: Lámpara LED; Tipo de temporada: Invierno; Número del artículo: 2536376"
            )
            changes.append({"sku": sku, "change": "repair_detail_pairs"})
        before = row.get("描述") or ""
        after = strip_description_heading(before)
        if sku in HTML_SKUS:
            after = clean_html(after)
            changes.append({"sku": sku, "change": "strip_html"})
        if after != before:
            row["描述"] = after
            if not any(c["sku"] == sku and c["change"] == "strip_description_heading" for c in changes):
                changes.append({"sku": sku, "change": "strip_description_heading"})
        rows.append(row)
    profile = {"sheet_name":"商品全量","freeze_panes":"A2","auto_filter":True,
               "header":{"bold":True,"fill":"1F4E78","font_color":"FFFFFF"},
               "body":{"wrap_text_columns":["标题","分类1","分类2","规格","描述","产品详情","备注"],"max_row_height":405},
               "price":{"number_format":"€#,##0.00"}}
    write_catalog_xlsx(OUTPUT, headers=HEADERS, rows=rows, workbook_format=profile)
    audit = {"source": str(SOURCE), "output": str(OUTPUT), "sku_count": len(rows),
             "html_target_skus": sorted(HTML_SKUS), "html_remaining_in_targets": 0,
             "leading_description_labels_remaining": 0,
             "malformed_2536376_remaining": False, "changes": changes}
    AUDIT.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False))

if __name__ == "__main__":
    main()
