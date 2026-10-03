"""Apply reviewed, source-bound repairs to a derived retranslation batch.

This is a no-Master, no-provider step.  Each replacement below is tied to a
known QA finding and preserves the immutable source fields.  The resulting
batch must still be replayed through Fact/Canonical QA before review.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
from collections import defaultdict
from pathlib import Path

from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.product_family import UNKNOWN_FAMILY, classify_product_family

BRAND_ALIASES_ZH = {
    "pepsi": ("百事可乐", "百事"), "huggies": ("好奇",),
    "comfibeds": ("康菲贝德",), "bic": ("百乐",), "mars": ("玛氏",),
    "7up": ("七喜",),
}


REPAIRS: dict[tuple[str, str], tuple[str, str]] = {
    # Preserve the source numeric fact that was omitted by the candidate.
    ("2506494", "description"): ("不含任何微塑料", "不含0.0%的微塑料"),
    ("2531979", "description"): ("不含任何微塑料", "不含0.0%的微塑料"),
    ("2508891", "details"): ("数量：400个；", "数量：400个；纸张数量：400张；"),
    # Ordinary English residuals.
    ("2516766", "description"): ("可 refill", "可补充"),
    ("2534566", "details"): ("是否可 refill：否", "是否可补充：否"),
    ("2549768", "details"): ("dispenser类型", "分配器类型"),
    # Display policy: remove translated brand spans while retaining product or
    # model/series information.
    ("2509140", "description"): ("Roshen润喉含片", "润喉含片"),
    ("2538428", "name"): ("百乐彩色记号笔", "彩色记号笔"),
    ("2538430", "name"): ("百乐彩色铅笔", "彩色铅笔"),
    ("2538431", "name"): ("百乐圆珠笔", "圆珠笔"),
    ("2549435", "name"): ("Ink & Print HP 364 XL 墨盒", "364 XL兼容墨盒"),
    ("2549435", "description"): ("多种惠普打印机", "多种打印机"),
    ("2531851", "name"): ("威利Nex微型汽车", "Nex微型汽车"),
    ("2550529", "description"): ("的Choco Trio", "Choco Trio"),
    # P0 family/context repairs from the v14 audit.
    ("2502085", "name"): ("XL", "XL礼品袋"),
    ("2501374", "name"): ("百事可乐零", "无糖可乐"),
    ("2501374", "description"): ("百事可乐零", "无糖可乐"),
    ("2501374", "details"): ("低脂版本：是", "轻盈版本：是"),
    # Owner-reviewed product noun/model repairs.
    ("2513658", "name"): ("七喜", "柠檬青柠汽水"),
    ("2526033", "name"): ("方格活页本", "A4方格活页本"),
    ("2534770", "name"): ("笔记本", "A4笔记本"),
    ("2544409", "name"): ("无骨雨刷片", "F48无骨雨刷片"),
    ("2544410", "name"): ("无骨雨刷片", "F45无骨雨刷片"),
    ("2544411", "name"): ("无骨雨刷片", "F40无骨雨刷片"),
    ("2548558", "name"): ("灯", "万圣节小灯笼"),
}

# These are Owner-approved whole-field values.  They must be applied even if
# the previous candidate no longer contains the old fragment (for example a
# provider may have returned `7Up` instead of the old Chinese value).
FORCED_REPAIRS = {
    ("2513658", "name"),
    ("2526033", "name"),
    ("2534770", "name"),
    ("2544409", "name"),
    ("2544410", "name"),
    ("2544411", "name"),
    ("2548558", "name"),
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    source_dir = Path(args.source_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    if output_dir.exists():
        shutil.rmtree(output_dir)
    shutil.copytree(source_dir, output_dir)
    rows = read_csv(output_dir / "retranslation_candidates.csv")
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row.get("sku", "")].append(row)
    applied: list[dict[str, str]] = []
    for row in rows:
        key = (row.get("sku", ""), row.get("field_name", ""))
        repair = REPAIRS.get(key)
        current = row.get("candidate_zh", "")
        updated = current
        old_fragment = ""
        new_fragment = ""
        if repair:
            old_fragment, new_fragment = repair
            if key in FORCED_REPAIRS:
                updated = new_fragment
            elif old_fragment in updated:
                updated = updated.replace(old_fragment, new_fragment, 1)
        # EMPTY_SOURCE_LOCALIZATION_CONTRACT_V1: no cross-field synthesis is
        # allowed by default.  Clear a candidate created for a source-empty
        # field; the downstream batch builder will mark it NO_SOURCE.
        if not str(row.get("source_es") or "").strip() and not str(row.get("old_zh") or "").strip():
            if updated:
                old_fragment, new_fragment = updated, ""
                updated = ""
        # The official context rule applies to every matching DIY category,
        # not only the first observed SKU.
        sku_rows = grouped.get(key[0], [row])
        record = {"sku": key[0]}
        for item in sku_rows:
            field = item.get("field_name", "")
            source_key = {"name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es", "spec": "spec_es", "description": "desc_es", "details": "details_es"}.get(field)
            if source_key:
                record[source_key] = item.get("source_es", "") or ""
        cat1 = str(record.get("cat1_es") or "").casefold()
        cat2 = str(record.get("cat2_es") or "").casefold()
        if key[1] == "cat2" and cat1 == "bricolaje" and cat2 == "complementos de pintura":
            if "绘画配件" in updated:
                old_fragment, new_fragment = "绘画配件", "涂料配件"
                updated = updated.replace(old_fragment, new_fragment, 1)
        if key[1] == "details" and cat1 == "bricolaje" and cat2 == "complementos de pintura":
            if "适用绘画类型" in updated:
                old_fragment, new_fragment = "适用绘画类型", "适用涂料类型"
                updated = updated.replace(old_fragment, new_fragment, 1)
        if key[1] == "details" and "variante ligera" in str(row.get("source_es") or "").casefold() and "低脂版本" in updated:
            old_fragment, new_fragment = "低脂版本", "轻盈版本"
            updated = updated.replace(old_fragment, new_fragment, 1)
        # Remove known Chinese brand aliases under the no-brand display
        # policy.  This is source-bound: an alias is removed only when the
        # corresponding source brand is present in the same SKU.
        source_all = " ".join(str(item.get("source_es") or "") for item in sku_rows).casefold()
        for source_brand, aliases in BRAND_ALIASES_ZH.items():
            if source_brand not in source_all:
                continue
            for alias in aliases:
                if alias in updated:
                    old_fragment, new_fragment = alias, ""
                    updated = updated.replace(alias, "", 1).strip()
        if key[1] == "name" and not updated.strip() and str(row.get("old_zh") or "").strip():
            old_fragment, new_fragment = current, str(row.get("old_zh") or "").strip()
            updated = new_fragment
        # Preserve product identity when a prior candidate collapsed to only a
        # size/model/technical token.  Use the existing product noun as the
        # safe baseline; retain an explicit size when the old value lacks it.
        if key[1] == "name" and updated and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .+\-/×xX]*", updated.strip()):
            old_value = str(row.get("old_zh") or "").strip()
            if old_value:
                old_fragment, new_fragment = updated, old_value
                if updated.casefold() in {"xl", "xxl"} and updated.casefold() not in old_value.casefold():
                    new_fragment = updated.upper() + old_value
                updated = new_fragment
        # If a previous family false positive generated a generic CLEANING_CLOTH
        # name, restore the existing product identity rather than carrying the
        # donor family output forward.  The old value is a baseline only; it is
        # used here strictly as a source-bound safety repair for a known bad
        # family classification.
        if key[1] == "name" and "清洁布" in updated:
            match = classify_product_family(SourceFacts.from_record(record))
            name_es = str(record.get("name_es") or "")
            if match.family_id == UNKNOWN_FAMILY and not any(term in name_es.casefold() for term in ("paño", "paños", "bayeta", "bayetas")):
                explicit_identity = (
                    (r"\blienzos?\b", "画布"),
                    (r"\bcordones\b", "鞋带"),
                    (r"\bapósitos adhesivos\b", "创可贴"),
                    (r"\bsistema de fregona\b", "拖把系统"),
                    (r"\bcolgadores de pared\b", "墙面挂钩"),
                    (r"\bmopa\b", "拖把"),
                )
                for pattern, identity in explicit_identity:
                    if re.search(pattern, name_es, re.I):
                        old_fragment, new_fragment = "清洁布", identity
                        updated = identity
                        break
                else:
                    old_value = row.get("old_zh", "")
                    if old_value.strip() and old_value.strip() != "清洁布":
                        old_fragment, new_fragment = "清洁布", old_value
                        updated = old_value
        if updated == current:
            continue
        row["candidate_zh"] = updated
        row["candidate_hash"] = hashlib.sha256(updated.encode("utf-8")).hexdigest()
        row["resolution_source"] = "DETERMINISTIC_RULE"
        row["provider"] = ""
        row["model"] = ""
        row["fact_qa_status"] = "NOT_RUN"
        row["canonical_qa_status"] = "NOT_RUN"
        row["qa_rule_id"] = ""
        row["qa_message"] = "DETERMINISTIC_REPAIR_PENDING_REQA"
        row["candidate_status"] = "REVIEW_REQUIRED"
        applied.append({
            "sku": key[0], "field_name": key[1],
            "old_fragment": old_fragment, "new_fragment": new_fragment,
            "repair_source": "DETERMINISTIC_RULE",
        })
    fields = list(rows[0]) if rows else []
    write_csv(output_dir / "retranslation_candidates.csv", rows, fields)
    write_csv(output_dir / "deterministic_repairs.csv", applied, [
        "sku", "field_name", "old_fragment", "new_fragment", "repair_source",
    ])
    (output_dir / "deterministic_repair_summary.json").write_text(
        json.dumps({"applied_count": len(applied), "production_writes": False}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"output_dir": str(output_dir), "applied_count": len(applied), "production_writes": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
