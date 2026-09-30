"""Create source-backed Chinese proposals for the staged review queue.

This artifact is review-only: it does not change product_dictionary.csv and
does not set Owner decisions.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


CAT2_ZH = {
    "Herramientas": "工具",
    "Detergentes y lavavajillas": "洗涤剂与洗碗机用品",
    "Productos de limpieza": "清洁用品",
    "Punto y ganchillo": "针织和钩编",
    "Ropa": "服装",
    "Peluches y muñecas": "毛绒玩具和玩偶",
    "Maletas": "行李箱",
    "Alimentación": "食品",
    "Coche": "汽车用品",
    "Cuidado corporal": "身体护理",
    "Sartenes": "煎锅",
    "Decoración": "装饰",
    "Manualidades": "手工制作",
    "Vajillas": "餐具",
    "Muebles": "家具",
    "Maquillaje": "彩妆",
    "Chocolate": "巧克力",
    "Pilas": "电池",
    "Colorear y dibujar": "涂色和绘画",
    "Cuidado del cabello": "护发",
    "Pintura": "油漆",
    "Almacenaje": "收纳用品",
    "Alimentación para animales": "宠物食品",
    "Artículos de baño y ducha": "洗浴和淋浴用品",
    "Cocina y repostería": "厨房与烘焙",
    "Artículos de papel": "纸制品",
}


def build(queue: Path, staging: Path, output: Path) -> dict[str, int]:
    rows = []
    with queue.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("field") != "cat2":
                continue
            es = (row.get("field_source_value_es") or "").strip()
            # A category proposal is safe only when the source itself is a
            # non-empty Spanish label.  Do not translate a contaminated field.
            if any("\u3400" <= char <= "\u9fff" or char == "�" for char in es):
                continue
            zh = CAT2_ZH.get(es)
            if not zh or (row.get("current_value_zh") or "").strip():
                continue
            row = dict(row)
            row.update({
                "proposed_value_zh": zh,
                "proposal_status": "MODEL_PREFILL_REVIEW_REQUIRED",
                "proposal_note": "由西语二级类目直译生成；需 Owner 确认后才能 Apply。",
            })
            rows.append(row)
    rows.sort(key=lambda row: row["sku"])
    output.parent.mkdir(parents=True, exist_ok=True)
    columns = list(rows[0]) if rows else ["sku", "field", "proposed_value_zh", "proposal_status", "proposal_note"]
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    manifest = {
        "proposal_type": "DICTIONARY_CAT2_TRANSLATION_PROPOSAL_V1",
        "proposal_count": len(rows),
        "owner_decisions": 0,
        "production_writes": 0,
        "status": "OWNER_REVIEW_REQUIRED",
        "csv": str(output),
    }
    output.with_name("dictionary_translation_proposals_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", default="runtime/dictionary/owner_review_20260915/current_dictionary_owner_review_queue.csv")
    parser.add_argument("--staging", default="runtime/dictionary/owner_review_20260915/source_restoration_staging.csv")
    parser.add_argument("--output", default="runtime/dictionary/owner_review_20260915/dictionary_translation_proposals.csv")
    args = parser.parse_args()
    print(json.dumps(build(Path(args.queue), Path(args.staging), Path(args.output)), ensure_ascii=False, indent=2))
