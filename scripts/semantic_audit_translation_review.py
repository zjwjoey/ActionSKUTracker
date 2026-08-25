"""对模型低置信翻译形成可复核的语义审查建议，不写入商品字典。"""
from __future__ import annotations

import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DICTIONARY = ROOT / "runtime" / "dictionary"

RECOMMENDATIONS = {
    "2557704": ("NEEDS_HUMAN_EVIDENCE", "", "饮料类但无描述、详情或有效规格；不能从 Troppie 猜测商品本体"),
    "3209615": ("RECOMMEND_REFINEMENT", "Furby Furblets电子宠物", "保留品牌/系列，补足中文商品主体"),
    "3209639": ("RECOMMEND_REFINEMENT", "Rodeo混合糖果", "保留名称 Rodeo，明确商品主体"),
    "3209865": ("RECOMMEND_REFINEMENT", "Gabby's Dollhouse玩具套装", "保留授权 IP，补足商品主体"),
    "3209915": ("RECOMMEND_REFINEMENT", "Absolu Chic Paris毛毯", "保留名称，补足商品主体"),
    "3209975": ("RECOMMEND_REFINEMENT", "锯齿纹装饰靠垫", "Zigzag 是图案描述，应翻译"),
    "3210263": ("RECOMMEND_REFINEMENT", "TCX防裂丙烯酸密封胶套装", "保留品牌 TCX，补全“密封胶”商品主体"),
    "3210285": ("RECOMMEND_REFINEMENT", "3M Command装饰夹", "保留品牌，补足商品主体"),
    "3219656": ("RECOMMEND_REFINEMENT", "Zed Candy USA硬糖", "保留品牌/系列，Jawbreaker 按硬糖表达"),
    "3223230": ("RECOMMEND_REFINEMENT", "Quality Street巧克力糖果", "保留产品品牌，商品主体明确为巧克力糖果"),
}


def read(name: str) -> list[dict[str, str]]:
    with (DICTIONARY / name).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    products = {row["sku"]: row for row in read("product_dictionary.csv")}
    source_rows = read("manual_review_model_quality.csv")
    output = []
    for source in source_rows:
        sku = source["sku"]
        product = products.get(sku, {})
        if product.get("locked") == "1" and product.get("review_status") == "HUMAN_REVIEWED":
            decision, recommended, rationale = "PASS_LOCKED", product.get("name_zh_standard", ""), "第二阶段人工审查结论已写入字段级锁定值"
        else:
            decision, recommended, rationale = RECOMMENDATIONS.get(
                sku, ("NEEDS_HUMAN_EVIDENCE", "", "缺少可确认商品本体的本地证据"),
            )
        output.append({
            "sku": sku,
            "source_last_seen": product.get("source_last_seen", source.get("source_last_seen", "")),
            "name_es": product.get("name_es_raw", source.get("name_es_raw", "")),
            "current_name_zh": product.get("name_zh_standard", source.get("name_zh_standard", "")),
            "spec_es": product.get("spec_es_raw", source.get("spec_es_raw", "")),
            "audit_decision": decision,
            "recommended_name_zh": recommended,
            "rationale": rationale,
            "apply_after_human_confirmation": "1" if decision == "RECOMMEND_REFINEMENT" else "0",
        })
    headers = list(output[0]) if output else ["sku"]
    with (DICTIONARY / "semantic_translation_audit.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(output)
    print({
        "rows": len(output),
        "locked_pass": sum(row["audit_decision"] == "PASS_LOCKED" for row in output),
        "recommended": sum(row["audit_decision"] == "RECOMMEND_REFINEMENT" for row in output),
        "needs_evidence": sum(row["audit_decision"] == "NEEDS_HUMAN_EVIDENCE" for row in output),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
