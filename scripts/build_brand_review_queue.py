"""生成品牌/IP/系列候选复核队列，不自动写入品牌字典。"""
from __future__ import annotations

import csv
import re
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from action_tracker.dictionary import (  # noqa: E402
    BRAND_DICTIONARY_HEADERS,
    PRODUCT_DICTIONARY_HEADERS,
    load_dictionary_rows,
)
import refine_dictionary_translations as review  # noqa: E402


LATIN_SEQUENCE = re.compile(r"[A-Za-z][A-Za-z0-9’'&.+/-]*(?:\s+[A-Za-z][A-Za-z0-9’'&.+/-]*)*")
HEADERS = [
    "sku", "source_hash", "source_last_seen", "name_es", "spec_es",
    "current_name_zh", "current_spec_zh", "latin_candidates", "issue_type",
    "decision", "canonical_entities", "confidence", "review_status", "notes",
]

ENTITY_PATTERNS = [
    (r"\bOptiSmile\b", "OptiSmile"), (r"\bZuru\b", "Zuru"), (r"\bChloe Girlz\b", "Chloe Girlz"),
    (r"\bFelix\b", "Felix"), (r"\bSensodyne\b", "Sensodyne"), (r"\bDulux\b", "Dulux"),
    (r"\bPlex Care\b", "Plex Care"), (r"\bTCX\b", "TCX"), (r"\bBites for Birds\b", "Bites for Birds"),
    (r"\bNYOY\b", "NYOY"), (r"\bGolden Glow\b", "Golden Glow"), (r"\bTimotei\b", "Timotei"),
    (r"\bGillette\b", "Gillette"), (r"\bHot Wheels\b", "Hot Wheels"), (r"\bDC Comics\b", "DC Comics"),
    (r"\bTeamsterz\b", "Teamsterz"), (r"\bLSC Smart Connect\b", "LSC Smart Connect"),
    (r"\bMagnifique\b", "Magnifique"), (r"\bThe Skin Dr\.?\b", "The Skin Dr."),
    (r"\bNuagé\b", "Nuagé"), (r"\bTomado\b", "Tomado"), (r"Fresh\s*[’']n\s*Rebel", "Fresh ’n Rebel"),
    (r"\bTMC Home\b", "TMC Home"), (r"\bAlpina\b", "Alpina"), (r"\bBIC\b", "BIC"),
    (r"\bFur Real\b", "FurReal"), (r"\bTummie Time\b", "Tummie Time"), (r"\bEnergizer\b", "Energizer"),
    (r"\bLowenthal\b", "Lowenthal"), (r"\bInnovit\b", "Innovit"), (r"\bSeasons & Style\b", "Seasons & Style"),
    (r"\bSlush Puppie\b", "Slush Puppie"), (r"\bHairmasters\b", "Hairmasters"),
    (r"\bCraft & Design\b", "Craft & Design"), (r"\bFuzzy Doodle\b", "Fuzzy Doodle"),
    (r"\bPetra\b", "Petra"), (r"\bLEGO\b|乐高", "LEGO"), (r"\bYoumi\b", "Youmi"),
    (r"\bHatakosen\b", "Hatakosen"), (r"\bLU\b", "LU"), (r"\bZed Candy\b", "Zed Candy"),
    (r"\bSlimy\b", "Slimy"), (r"\bMinecraft\b", "Minecraft"), (r"\bPAC-MAN\b", "PAC-MAN"),
    (r"\bBeyblade\b", "Beyblade"), (r"\bUNO\b|\bUno\b", "UNO"), (r"\bDream Lab\b", "Dream Lab"),
    (r"\bChupa Chups\b", "Chupa Chups"), (r"\bMogu Mogu\b", "Mogu Mogu"),
    (r"\bTapo\b", "Tapo"), (r"\bPlaymobil\b", "Playmobil"), (r"\bRevolution\b", "Revolution"),
    (r"\bBuzz Lightyear\b", "Buzz Lightyear"), (r"\bHobby Flora\b", "Hobby Flora"),
    (r"\bZenova\b", "Zenova"), (r"\bNicols\b", "Nicols"),
    (r"\bHarry Potter\b", "Harry Potter"), (r"Crafts\s*&\s*Co", "Crafts & Co"),
    (r"Reese(?:'|&apos;)s", "Reese's"),
]
NON_BRAND_SKUS = {"2559113", "3205379", "3208771", "3210997", "3217483", "3218848", "3222273"}
SERIES_STYLE_SKUS = {
    "3015660", "3203320", "3217312", "3217317", "3217329", "3218643", "3218645",
    "3218646", "3218648", "3218649", "3218950", "3220450", "3222225", "3222238", "3222294",
    "3013648",
}


def _decision(item: dict[str, str]) -> tuple[str, str, str, str]:
    if item["sku"] in NON_BRAND_SKUS:
        return "NON_BRAND_TERM", "", "HIGH", "普通词、工艺词或技术缩写，不进入品牌字典"
    if item["sku"] in SERIES_STYLE_SKUS:
        return "PRODUCT_SERIES_OR_STYLE", "", "MEDIUM", "商品系列、款式或型号，可保留但不进入品牌字典"
    evidence = item["name_es"] + " " + item["current_name_zh"]
    entities = []
    for pattern, canonical in ENTITY_PATTERNS:
        if re.search(pattern, evidence, re.I) and canonical not in entities:
            entities.append(canonical)
    if entities:
        return "CONFIRMED_BRAND_OR_IP", " | ".join(entities), "HIGH", "商品原名中有明确品牌/IP证据"
    return "STYLE_OR_MODEL_REVIEW", "", "LOW", "可能是系列、款式或型号，证据不足，不自动入品牌字典"


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    dictionary = root / "runtime" / "dictionary"
    output = root / "runtime" / "dictionary" / "brand_review_queue.csv"
    products = load_dictionary_rows(dictionary / "product_dictionary.csv", headers=PRODUCT_DICTIONARY_HEADERS, key_fields=("sku",))
    brands = sorted({row["canonical_name"] for row in load_dictionary_rows(
        dictionary / "brand_dictionary.csv", headers=BRAND_DICTIONARY_HEADERS, key_fields=("brand_id",),
    ) if row["canonical_name"]}, key=len, reverse=True)
    candidates = review._candidates(products, brands)
    rows = []
    for item in candidates:
        if review.CJK.search(item["name_es"] + item["spec_es"]):
            continue
        latin = LATIN_SEQUENCE.findall(item["current_name_zh"])
        issue = "BRAND_OR_IP_REVIEW" if item["name_bad"] else "SPEC_TECHNICAL_REVIEW"
        decision, entities, confidence, decision_note = _decision(item)
        rows.append({
            "sku": item["sku"], "source_hash": item["source_hash"], "source_last_seen": item["source_last_seen"],
            "name_es": item["name_es"], "spec_es": item["spec_es"],
            "current_name_zh": item["current_name_zh"], "current_spec_zh": item["current_spec_zh"],
            "latin_candidates": " | ".join(latin), "issue_type": issue,
            "decision": decision, "canonical_entities": entities, "confidence": confidence,
            "review_status": "已规则审核" if confidence in {"HIGH", "MEDIUM"} else "待人工核验",
            "notes": decision_note,
        })
    rows.sort(key=lambda row: row["sku"])
    output.write_text("", encoding="utf-8")
    with output.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(rows)
    print({
        "output": str(output), "rows": len(rows),
        "confirmed": sum(row["decision"] == "CONFIRMED_BRAND_OR_IP" for row in rows),
        "non_brand": sum(row["decision"] == "NON_BRAND_TERM" for row in rows),
        "series_or_style": sum(row["decision"] == "PRODUCT_SERIES_OR_STYLE" for row in rows),
        "needs_review": sum(row["decision"] == "STYLE_OR_MODEL_REVIEW" for row in rows),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
