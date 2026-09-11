"""Final mechanical format fix for the Spanish research export.

Scope is intentionally limited to removing duplicate trailing colons in the
product-details field. All other workbook values are preserved and only
read-only QC checks are performed after the edit.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from openpyxl import load_workbook

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SOURCE = Path(
    r"F:\ActionSKUTracker\runtime\exports\20260907Action商品全量_西班牙语版_不带图_最终修复版_分类清理版.xlsx"
)
OUTPUT = SOURCE.with_name("20260907Action商品全量_西班牙语版_正式研究版.xlsx")
AUDIT = OUTPUT.with_suffix(".audit.json")

DOUBLE_COLON_RE = re.compile(r"(^|[;\n]\s*)([^;:\n]+?)\s*:+\s*", re.MULTILINE)


def fix_detail_format(value: object) -> tuple[str, int]:
    text = "" if value is None else str(value)
    changed = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal changed
        prefix = match.group(1)
        field = match.group(2).strip().rstrip(":").strip()
        changed += 1
        return f"{prefix}{field}: "

    return DOUBLE_COLON_RE.sub(replace, text), changed


def contains_html_or_ui_residue(text: str) -> bool:
    if re.search(r"<[^>]+>|</\*|\b(?:null|undefined)\b", text, flags=re.I):
        return True
    if re.search(r"(?im)^\s*Leer más\s*$", text):
        return True
    if re.search(r"(?im)^\s*Descripción\s*$", text):
        return True
    return "> >" in text


def sku_from_url(url: object) -> str | None:
    match = re.search(r"/p/(\d+)/", str(url or ""))
    return match.group(1) if match else None


def detail_sku(detail: object) -> str | None:
    match = re.search(r"Número del artículo\s*:\s*(\d+)", str(detail or ""), flags=re.I)
    return match.group(1) if match else None


def main() -> None:
    wb = load_workbook(SOURCE, data_only=False)
    ws = wb.active
    headers = [cell.value for cell in ws[1]]
    idx = {str(h): i + 1 for i, h in enumerate(headers)}
    required = {
        "编号": 2,
        "标题": 3,
        "分类1": 4,
        "分类2": 5,
        "折后价": 7,
        "单价": 9,
        "描述": 10,
        "产品详情": 11,
        "图片链接": 12,
        "商品链接": 13,
    }

    before_double_colon = 0
    modified_skus: list[str] = []
    modified_cell_count = 0
    all_rows: list[tuple] = []

    # Only column K / 产品详情 is written, and only when the exact mechanical
    # duplicate-colon pattern is found.
    for row in ws.iter_rows(min_row=2, values_only=False):
        sku = str(row[1].value or "").strip()
        detail_cell = row[10]
        original = "" if detail_cell.value is None else str(detail_cell.value)
        before_double_colon += original.count("::")
        fixed, changed = fix_detail_format(original)
        if changed and fixed != original:
            detail_cell.value = fixed
            modified_cell_count += 1
            modified_skus.append(sku)

    # Save a new workbook; never overwrite the source.
    wb.save(OUTPUT)
    wb.close()

    # Re-open the generated artifact and run the requested final QC.
    check_wb = load_workbook(OUTPUT, read_only=False, data_only=True)
    check_ws = check_wb.active
    rows = list(check_ws.iter_rows(min_row=2, values_only=True))
    skus = [str(row[1]).strip() for row in rows]
    duplicate_skus = len(skus) - len(set(skus))
    legal_sku_errors = sum(not re.fullmatch(r"\d+", sku) for sku in skus)

    required_blank_fields: dict[str, int] = {}
    for name, col in required.items():
        required_blank_fields[name] = sum(
            row[col - 1] is None or not str(row[col - 1]).strip() for row in rows
        )

    original_price_blank_count = sum(row[7] is None or not str(row[7]).strip() for row in rows)
    discounted_sku_count = len(rows) - original_price_blank_count
    price_logic_error_count = sum(
        row[7] is not None
        and str(row[7]).strip()
        and row[6] is not None
        and float(row[7]) <= float(row[6])
        for row in rows
    )

    sku_url_mismatches = []
    sku_detail_mismatches = []
    html_residue_rows = []
    null_undefined_rows = []
    remaining_format_issues: list[dict] = []
    after_double_colon = 0
    for row in rows:
        sku = str(row[1]).strip()
        detail = "" if row[10] is None else str(row[10])
        product_url = row[12]
        url_sku = sku_from_url(product_url)
        if url_sku != sku:
            sku_url_mismatches.append(sku)
        detail_id = detail_sku(detail)
        if detail_id != sku:
            sku_detail_mismatches.append(sku)
        all_text = "\n".join(str(row[i] or "") for i in (2, 3, 4, 5, 9, 10, 13))
        if contains_html_or_ui_residue(all_text):
            html_residue_rows.append(sku)
        if re.search(r"\b(?:null|undefined)\b", all_text, flags=re.I):
            null_undefined_rows.append(sku)
        count = detail.count("::")
        after_double_colon += count
        for issue in ("::", ": ;", ";;", "\t", "\n"):
            if issue in detail:
                remaining_format_issues.append({"sku": sku, "issue": repr(issue)})
                break

    freeze_panes = str(check_ws.freeze_panes)
    auto_filter = check_ws.auto_filter.ref
    check_wb.close()

    required_blank_total = sum(required_blank_fields.values())
    qc_result = "PASS" if all(
        [
            len(rows) == 5552,
            duplicate_skus == 0,
            legal_sku_errors == 0,
            required_blank_total == 0,
            price_logic_error_count == 0,
            len(sku_url_mismatches) == 0,
            len(sku_detail_mismatches) == 0,
            after_double_colon == 0,
            len(html_residue_rows) == 0,
            len(null_undefined_rows) == 0,
            len(remaining_format_issues) == 0,
        ]
    ) else "FAIL"

    audit = {
        "source_file": str(SOURCE),
        "output_file": str(OUTPUT),
        "sku_count": len(rows),
        "duplicate_sku_count": duplicate_skus,
        "legal_sku_error_count": legal_sku_errors,
        "double_colon_before": before_double_colon,
        "double_colon_after": after_double_colon,
        "modified_sku_count": len(set(modified_skus)),
        "modified_cell_count": modified_cell_count,
        "modified_skus": sorted(set(modified_skus)),
        "required_blank_fields": required_blank_fields,
        "original_price_blank_count": original_price_blank_count,
        "discounted_sku_count": discounted_sku_count,
        "price_logic_error_count": price_logic_error_count,
        "sku_url_mismatch_count": len(sku_url_mismatches),
        "sku_url_mismatches": sku_url_mismatches,
        "sku_detail_mismatch_count": len(sku_detail_mismatches),
        "sku_detail_mismatches": sku_detail_mismatches,
        "html_residue_count": len(html_residue_rows),
        "html_residue_skus": html_residue_rows,
        "null_undefined_count": len(null_undefined_rows),
        "null_undefined_skus": null_undefined_rows,
        "remaining_format_issues": remaining_format_issues,
        "freeze_panes": "A2" if freeze_panes == "A2" else freeze_panes,
        "auto_filter": auto_filter,
        "qc_result": qc_result,
    }
    AUDIT.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print("正式研究版最终QC")
    print(f"SKU总数：{audit['sku_count']}")
    print(f"修改SKU数：{audit['modified_sku_count']}")
    print(f"修改单元格数：{audit['modified_cell_count']}")
    print(f"双冒号 修复前：{audit['double_colon_before']}")
    print(f"双冒号 修复后：{audit['double_colon_after']}")
    print(f"必填字段空值：{required_blank_total}")
    print(f"价格逻辑异常：{audit['price_logic_error_count']}")
    print(f"SKU-URL不一致：{audit['sku_url_mismatch_count']}")
    print(f"SKU-详情编号不一致：{audit['sku_detail_mismatch_count']}")
    print(f"HTML残留：{audit['html_residue_count']}")
    print(f"null/undefined残留：{audit['null_undefined_count']}")
    print(f"其他格式异常：{len(audit['remaining_format_issues'])}")
    print(f"QC结果：{audit['qc_result']}")


if __name__ == "__main__":
    main()
