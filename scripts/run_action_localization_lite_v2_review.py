"""Run the Lite V2 review pass without invoking Qwen or touching Master.

The input is the already completed Lite V1 sample and its original qwen_zh
results.  V2 changes only review priority and output projection; it does not
retranslate, call a provider, or apply any production patch.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")
SOURCE_FOR = {"name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es", "spec": "spec_es", "description": "description_es", "details": "details_es"}
NO_SOURCE = {"description", "details"}
SPANISH_MARKERS = re.compile(r"\b(?:de|del|la|el|los|las|para|con|sin|una|uno|un|y|en|por|más|color|colores|tamaño|unidades|piezas|pack|set)\b", re.I)
NUMBER_TOKEN = re.compile(r"(?<![A-Za-z0-9])(?:\d+(?:[.,]\d+)?|[A-Z]{1,4}\d+[A-Z0-9-]*|\d+[A-Z][A-Z0-9-]*)(?![A-Za-z0-9])", re.I)
MODEL_TOKEN = re.compile(r"(?<![A-Za-z0-9])(?:A\d{1,3}|F\d{2,3}|XL|XXL|[A-Z]{1,4}\d+[A-Z0-9-]*)(?![A-Za-z0-9])", re.I)
HTML = re.compile(r"<[^>]+>|\b(?:null|undefined|Leer más|Descripción)\b", re.I)

# Known Chinese display aliases used only to detect/remove a source brand from
# a Qwen name.  Unknown or ambiguous brand retention is routed to review.
BRAND_ZH = {
    "7up": ("七喜",), "c&c": (), "capetown": ("开普敦",), "comfibeds": ("康菲贝德",),
    "excellent houseware": (), "intex": ("英特斯",), "medisana": ("美迪莎纳",),
    "nature fit": (), "oreo": ("奥利奥",), "panasonic": ("松下",), "patisserie": ("帕提斯里",),
    "pepsi": ("百事", "百事可乐"), "roshen": ("罗申", "罗森"), "samba": ("桑巴",),
    "spargo": ("斯帕戈",), "spectrum": ("光谱",), "teddy care": ("泰迪呵护",),
    "thermofect": (), "trolli": ("特洛利",), "twix": ("趣多多",), "van bleiswijck": ("范布莱斯韦克",),
    "werckmann": ("维尔克曼",), "werther's original": ("维尔特",), "welly": ("威利",),
    "whiskas": ("威斯卡斯",), "yammie": ("雅米",), "bref": (), "tcx": (),
}

SPECIAL_NAME_CORRECTIONS = {
    # These source names are brand-only or brand-dominant.  The product noun
    # is unambiguous from the same SKU's category/description context, so the
    # no-brand display value can be corrected without a provider call.
    "7up": ("柠檬味汽水", "BRAND_POLICY;PRODUCT_IDENTITY", "removed brand 7Up and restored the beverage identity from lemon/lime and carbonated context"),
    "oreo original": ("原味夹心饼干", "BRAND_POLICY;PRODUCT_IDENTITY", "removed Oreo and retained the original sandwich-cookie identity from category/description context"),
    "pepsi max": ("无糖可乐", "BRAND_POLICY;PRODUCT_IDENTITY", "removed Pepsi and rendered Max as the source-supported no-sugar cola identity"),
    "twix": ("巧克力焦糖饼干", "BRAND_POLICY;PRODUCT_IDENTITY", "removed Twix and retained the chocolate-caramel biscuit identity from description/details"),
}

PRODUCT_EXPECTATIONS = {
    "cuaderno": ("本", "活页", "笔记"), "galleta": ("饼干",), "galletas": ("饼干",),
    "rodillo": ("滚筒", "滚刷"), "rodillos": ("滚筒", "滚刷"), "colchón": ("床垫",),
    "bolsa": ("袋", "包"), "bolsas": ("袋", "包"), "tendedero": ("晾衣",), "pastilla": ("片", "块"),
    "pastillas": ("片", "块"), "mopa": ("拖把",), "cepillo": ("刷",), "toalla": ("巾",),
    "boquilla": ("吸嘴",), "espray": ("喷雾",), "tela": ("布", "面料", "篷布"), "lienzo": ("画布",),
    "pila": ("电池",), "guantes": ("手套",), "caramelos": ("糖果",), "golosinas": ("零食", "糖"),
    "sticks": ("棒", "条"), "barritas": ("棒", "条"), "termómetro": ("体温计",), "salsa": ("酱",),
    "almohadilla": ("垫",), "almohadillas": ("垫",), "edredón": ("被",), "hilo": ("线",),
    "cartuchos": ("墨盒",), "brochas": ("刷",), "papel": ("纸",), "estropajos": ("百洁布", "钢丝球"),
    "mesa": ("桌",), "servilletas": ("餐巾",), "posavasos": ("杯垫",), "guantes": ("手套",),
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def write_csv(path: Path, rows: Iterable[Mapping[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: "" if row.get(key) is None else row.get(key, "") for key in fieldnames})


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_brand_aliases(path: Path) -> tuple[str, ...]:
    if not path.exists():
        return ()
    aliases: set[str] = set()
    for row in read_csv(path):
        for key in ("canonical_name", "aliases_es"):
            for alias in re.split(r"[|;/]", str(row.get(key) or "")):
                alias = alias.strip()
                if len(alias) >= 3:
                    aliases.add(alias)
    return tuple(sorted(aliases, key=lambda item: (-len(item), item.casefold())))


def group(rows: list[dict[str, str]]) -> dict[str, dict[str, dict[str, str]]]:
    grouped: dict[str, dict[str, dict[str, str]]] = {}
    for row in rows:
        sku, field = row.get("sku", ""), row.get("field_name", "")
        if sku and field in FIELDS:
            grouped.setdefault(sku, {})[field] = row
    return grouped


def find_source_brands(source_name: str, aliases: tuple[str, ...]) -> list[str]:
    lower = source_name.casefold()
    return [alias for alias in aliases if re.search(rf"(?<!\w){re.escape(alias.casefold())}(?!\w)", lower)]


def contains_brand_target(text: str, brand: str) -> bool:
    lower = text.casefold()
    if re.search(rf"(?<!\w){re.escape(brand.casefold())}(?!\w)", lower):
        return True
    return any(alias and alias in text for alias in BRAND_ZH.get(brand.casefold(), ()))


def strip_brand(text: str, brand: str) -> str:
    value = text
    value = re.sub(rf"(?<!\w){re.escape(brand)}(?!\w)", " ", value, flags=re.I)
    for alias in BRAND_ZH.get(brand.casefold(), ()):
        value = value.replace(alias, " ")
    value = re.sub(r"^\s*(?:牌|品牌)\s*", "", value)
    value = re.sub(r"\s{2,}", " ", value)
    value = re.sub(r"^[\s·,，:/：-]+|[\s·,，:/：-]+$", "", value)
    return value.strip()


def source_tokens(source_name: str) -> list[str]:
    return [token.casefold() for token in MODEL_TOKEN.findall(source_name)]


def token_present(token: str, target: str) -> bool:
    low = target.casefold()
    if token.casefold() in low:
        return True
    if token.casefold() in {"xl", "xxl"}:
        return any(size in target for size in ("超大", "加大", "特大"))
    return False


def expected_product(source_name: str) -> tuple[str, ...]:
    low = source_name.casefold()
    expected: list[str] = []
    for term, targets in PRODUCT_EXPECTATIONS.items():
        if re.search(rf"(?<!\w){re.escape(term)}", low):
            expected.extend(targets)
    return tuple(dict.fromkeys(expected))


def review_name(row: Mapping[str, str], context: Mapping[str, Mapping[str, str]], aliases: tuple[str, ...]) -> tuple[str, str, str, str]:
    source = str(row.get("source_es") or "")
    qwen = str(row.get("qwen_zh") or "")
    if not source.strip():
        return qwen, "KEEP", "", "P0 context review: no source name"
    if not qwen.strip() or (not re.search(r"[\u3400-\u9fff]", qwen) and SPANISH_MARKERS.search(source)):
        return qwen, "REVIEW_REQUIRED", "MISTRANSLATION", "P0 name has no reliable Chinese product name"

    target = qwen
    corrections: list[str] = []
    notes: list[str] = []
    brands = find_source_brands(source, aliases)
    special = SPECIAL_NAME_CORRECTIONS.get(source.casefold().strip())
    if special:
        return special[0], "CORRECTED", special[1], special[2]
    for brand in brands:
        if contains_brand_target(target, brand):
            stripped = strip_brand(target, brand)
            if stripped and stripped != target:
                if len(stripped) < 2 or not re.search(r"[\u3400-\u9fffA-Za-z]", stripped):
                    return target, "REVIEW_REQUIRED", "BRAND_POLICY", f"brand {brand} retained but safe removal leaves no reliable product identity"
                target = stripped
                corrections.append("BRAND_POLICY")
                notes.append(f"removed display brand {brand}")
            else:
                return qwen, "REVIEW_REQUIRED", "BRAND_POLICY", f"brand {brand} retained but safe removal would erase product identity"

    missing_tokens = [token for token in source_tokens(source) if not token_present(token, target)]
    if missing_tokens:
        # A4/Fxx are unambiguous identifiers and can be restored without a
        # provider call; other tokens stay in the owner queue.
        if all(re.fullmatch(r"A\d{1,3}|F\d{2,3}", token, re.I) for token in missing_tokens):
            target = " ".join(token.upper() for token in missing_tokens) + target
            corrections.append("MODEL_OR_SPEC")
            notes.append(f"restored identifiers {','.join(token.upper() for token in missing_tokens)}")
        else:
            return target, "REVIEW_REQUIRED", "MODEL_OR_SPEC", f"name identifier(s) missing: {','.join(missing_tokens)}"

    expected = expected_product(source)
    if expected and not any(term in target for term in expected):
        # Use context to make the gate explainable; do not invent a noun.
        context_text = " ".join(str(context.get(field, {}).get("source_es") or "") for field in ("cat1", "cat2", "spec", "description", "details"))
        return target, "REVIEW_REQUIRED", "PRODUCT_IDENTITY", f"P0 product identity needs owner confirmation; source context: {context_text[:180]}"

    if corrections:
        return target, "CORRECTED", ";".join(dict.fromkeys(corrections)), "; ".join(notes)
    return target, "KEEP", "", "P0 context review: product identity, context, and naming policy confirmed"


def review_light(row: Mapping[str, str], priority: str) -> tuple[str, str, str, str]:
    source = str(row.get("source_es") or "")
    qwen = str(row.get("qwen_zh") or "")
    if not source.strip():
        return qwen, "KEEP", "", f"{priority} source field empty; no translation required"
    if not qwen.strip():
        return qwen, "REVIEW_REQUIRED", "MISSING_FACT", f"{priority} translated value is empty"
    if HTML.search(qwen) or (not re.search(r"[\u3400-\u9fff]", qwen) and SPANISH_MARKERS.search(source)):
        return qwen, "REVIEW_REQUIRED", "MISTRANSLATION", f"{priority} contains residual markup or Spanish text"
    if priority == "P1":
        src_nums = set(NUMBER_TOKEN.findall(source))
        missing = [token for token in src_nums if token.casefold() not in qwen.casefold()]
        if missing:
            return qwen, "REVIEW_REQUIRED", "NUMERIC_OR_UNIT", f"P1 protected token(s) missing: {','.join(missing)}"
    return qwen, "KEEP", "", f"{priority} anomaly scan passed; no style rewrite"


def main() -> int:
    parser = argparse.ArgumentParser(description="Review the fixed Lite 150 sample with V2 priorities; no provider calls")
    parser.add_argument("--v1-dir", default=r"F:\ActionSKUTracker\runtime\localization\lite_v1_150")
    parser.add_argument("--output-dir", default=r"F:\ActionSKUTracker\runtime\localization\lite_v2_150")
    parser.add_argument("--brand-dictionary", default=r"F:\ActionSKUTracker\runtime\dictionary\brand_dictionary.csv")
    args = parser.parse_args()
    v1_dir, output, brand_path = Path(args.v1_dir), Path(args.output_dir), Path(args.brand_dictionary)
    output.mkdir(parents=True, exist_ok=True)
    v1_rows = read_csv(v1_dir / "lite_review_rows.csv")
    sample = read_csv(v1_dir / "lite_150_sample_manifest.csv")
    if len(sample) != 150 or len({row.get("sku") for row in sample}) != 150:
        raise SystemExit("LITE_V2_SAMPLE_INVALID")
    grouped = group(v1_rows)
    aliases = load_brand_aliases(brand_path)
    v2_rows: list[dict[str, str]] = []
    findings: list[dict[str, str]] = []
    p0_counts = Counter(); p1_counts = Counter(); p2_counts = Counter()
    for sku in [row["sku"] for row in sample]:
        context = grouped.get(sku, {})
        for field in FIELDS:
            row = context.get(field, {})
            priority = "P0" if field == "name" else "P1" if field in {"cat1", "cat2", "spec"} else "P2"
            if priority == "P0":
                reviewed, decision, correction, note = review_name(row, context, aliases)
            else:
                reviewed, decision, correction, note = review_light(row, priority)
            out = {
                "sku": sku, "field_name": field, "priority": priority,
                "source_es": row.get("source_es", ""), "qwen_zh": row.get("qwen_zh", ""),
                "reviewed_zh_v1": row.get("reviewed_zh", ""), "reviewed_zh_v2": reviewed,
                "decision_v1": row.get("review_decision", ""), "decision_v2": decision,
                "correction_type": correction, "review_note": note,
                "cat1_es": context.get("cat1", {}).get("source_es", ""),
                "cat2_es": context.get("cat2", {}).get("source_es", ""),
                "spec_es": context.get("spec", {}).get("source_es", ""),
                "description_es": context.get("description", {}).get("source_es", ""),
                "details_es": context.get("details", {}).get("source_es", ""),
            }
            v2_rows.append(out)
            (p0_counts if priority == "P0" else p1_counts if priority == "P1" else p2_counts)[decision] += 1
            if decision != "KEEP":
                findings.append({key: out[key] for key in ("sku", "field_name", "priority", "source_es", "qwen_zh", "reviewed_zh_v1", "reviewed_zh_v2", "decision_v1", "decision_v2", "correction_type", "review_note")})

    columns = list(v2_rows[0])
    write_csv(output / "lite_translation_150_v2.csv", v2_rows, columns)
    write_csv(output / "lite_translation_review_findings_v2.csv", findings, ["sku", "field_name", "priority", "source_es", "qwen_zh", "reviewed_zh_v1", "reviewed_zh_v2", "decision_v1", "decision_v2", "correction_type", "review_note"])
    write_csv(output / "lite_150_sample_manifest_v2.csv", sample, list(sample[0]))
    name_rows = []
    for sku in [row["sku"] for row in sample]:
        name = next(row for row in v2_rows if row["sku"] == sku and row["field_name"] == "name")
        name_rows.append({
            "sku": sku, "name_es": name["source_es"], "name_qwen_zh": name["qwen_zh"],
            "name_reviewed_zh_v1": name["reviewed_zh_v1"], "name_reviewed_zh_v2": name["reviewed_zh_v2"],
            "decision_v1": name["decision_v1"], "decision_v2": name["decision_v2"],
            "correction_type": name["correction_type"], "review_note": name["review_note"],
            "cat1_es": name["cat1_es"], "cat2_es": name["cat2_es"], "spec_es": name["spec_es"],
        })
    write_csv(output / "lite_name_review_150_v2.csv", name_rows, list(name_rows[0]))
    owner = [row for row in v2_rows if row["decision_v2"] == "REVIEW_REQUIRED"]
    write_csv(output / "lite_owner_review_queue_v2.csv", owner, columns)
    summary = {
        "status": "READY_FOR_OWNER_REVIEW", "sample_unchanged": True, "sku_count": 150, "field_count": 900,
        "p0_name_reviewed": 150, "p0_name_counts": dict(p0_counts), "p1_counts": dict(p1_counts), "p2_counts": dict(p2_counts),
        "review_keep": sum(row["decision_v2"] == "KEEP" for row in v2_rows),
        "review_corrected": sum(row["decision_v2"] == "CORRECTED" for row in v2_rows),
        "review_required": sum(row["decision_v2"] == "REVIEW_REQUIRED" for row in v2_rows),
        "name_review_required": sum(row["decision_v2"] == "REVIEW_REQUIRED" for row in name_rows),
        "qwen_calls": 0, "qwen_reused_rows": 900, "provider": "", "model": "",
        "master_writes": 0, "production_apply": False,
        "source_qwen_csv_sha256": sha256_file(v1_dir / "lite_qwen_results.csv"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "artifacts": {
            "full_csv": str(output / "lite_translation_150_v2.csv"),
            "name_csv": str(output / "lite_name_review_150_v2.csv"),
            "findings_csv": str(output / "lite_translation_review_findings_v2.csv"),
            "owner_queue_csv": str(output / "lite_owner_review_queue_v2.csv"),
        },
    }
    (output / "lite_v2_review_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
