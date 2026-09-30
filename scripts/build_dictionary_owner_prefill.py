"""Build a complete Owner prefill for source-restored dictionary rows."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

CAT2 = {
    "Herramientas": "工具", "Detergentes y lavavajillas": "洗涤剂与洗碗机用品",
    "Productos de limpieza": "清洁用品", "Punto y ganchillo": "针织和钩编",
    "Ropa": "服装", "Peluches y muñecas": "毛绒玩具和玩偶", "Maletas": "行李箱",
    "Alimentación": "食品", "Coche": "汽车用品", "Cuidado corporal": "身体护理",
    "Sartenes": "煎锅", "Decoración": "装饰", "Manualidades": "手工制作",
    "Vajillas": "餐具", "Muebles": "家具", "Maquillaje": "彩妆", "Chocolate": "巧克力",
    "Pilas": "电池", "Colorear y dibujar": "涂色和绘画", "Cuidado del cabello": "护发",
    "Pintura": "油漆", "Almacenaje": "收纳用品", "Alimentación para animales": "宠物食品",
    "Artículos de baño y ducha": "洗浴和淋浴用品", "Cocina y repostería": "厨房与烘焙",
    "Artículos de papel": "纸制品",
}


def _text(v: object) -> str:
    return "" if v is None else str(v).strip()


def build(dictionary: Path, staging: Path, output: Path) -> dict[str, int]:
    products = {r["sku"]: r for r in csv.DictReader((dictionary / "product_dictionary.csv").open(encoding="utf-8-sig", newline=""))}
    staged = list(csv.DictReader(staging.open(encoding="utf-8-sig", newline="")))
    rows = []
    for source in staged:
        product = products[source["sku"]]
        values = {
            "name": (_text(product.get("name_zh_standard")), "保留当前中文品名"),
            "spec": (_text(product.get("spec_zh_standard")), "保留当前中文规格"),
            "cat1": (_text(product.get("cat1_zh")), "保留已确认一级类目"),
            "cat2": (_text(product.get("cat2_zh")), "保留当前二级类目"),
        }
        if not values["cat2"][0] and not any("\u3400" <= c <= "\u9fff" or c == "�" for c in source.get("cat2_es_new", "")):
            candidate = CAT2.get(source.get("cat2_es_new", ""))
            if candidate:
                values["cat2"] = (candidate, "根据西语二级类目生成候选")
        for field, (value, note) in values.items():
            rows.append({
                "sku": source["sku"], "field": field,
                "source_hash": source["new_source_hash"],
                "source_value_es": source.get({"name": "name_es_raw_new", "spec": "spec_es_raw_new", "cat1": "cat1_es_new", "cat2": "cat2_es_new"}[field], ""),
                "current_value_zh": product.get({"name": "name_zh_standard", "spec": "spec_zh_standard", "cat1": "cat1_zh", "cat2": "cat2_zh"}[field], ""),
                "proposed_value_zh": value,
                "proposed_action": "PROPOSE_APPROVE" if value else "SOURCE_EVIDENCE_REQUIRED",
                "proposal_note": note if value else "中文值为空，不能自动补写",
                "owner_decision": "",
                "owner_value_zh": "",
                "owner_note": "",
            })
    rows.sort(key=lambda r: (r["sku"], r["field"]))
    output.parent.mkdir(parents=True, exist_ok=True)
    columns = list(rows[0]) if rows else ["sku"]
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns); writer.writeheader(); writer.writerows(rows)
    manifest = {
        "queue_type": "DICTIONARY_OWNER_PREFILL_V1", "staged_sku_count": len(staged),
        "prefill_field_count": len(rows), "owner_decisions": 0, "production_writes": 0,
        "status": "OWNER_REVIEW_REQUIRED", "csv": str(output),
    }
    output.with_name("dictionary_owner_prefill_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--dictionary", default="runtime/dictionary"); parser.add_argument("--staging", default="runtime/dictionary/owner_review_20260915/source_restoration_staging.csv"); parser.add_argument("--output", default="runtime/dictionary/owner_review_20260915/dictionary_owner_prefill.csv")
    args = parser.parse_args(); print(json.dumps(build(Path(args.dictionary), Path(args.staging), Path(args.output)), ensure_ascii=False, indent=2))
