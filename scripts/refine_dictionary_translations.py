"""以 DeepSeek 复核字典中残留的普通西语/英语；品牌、型号与技术缩写保留。"""
from __future__ import annotations

import argparse
import csv
from functools import lru_cache
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from action_tracker.config import load_settings
from action_tracker.dictionary import (
    MODEL_TRANSLATION_HEADERS,
    load_dictionary_rows,
    write_dictionary_csv,
)


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")


SPANISH_MARKERS = re.compile(
    r"\b(?:el|la|los|las|de|del|con|para|sin|una|un|unos|unas|varios|varias|diferentes|"
    r"talla|tallas|unidades?|piezas?|gramos?|metros?|litros?|paquete|juego|calcetines|"
    r"mallas|pantis|guantes|gorro|chocolate|cable|bolsa|discos|gel|colores?)\b", re.I,
)
ALLOWED_LATIN = re.compile(
    r"(?:USB(?:-C)?|HDMI|RGB|Wi-?Fi|NFC|GPS|OLED|LCD|ANC|SPF|FPS|FM|OK|pH|FSC|BPA|"
    r"micro-USB|microSD|MagSafe|Qi\d*|iPhone|iOS|QHD|Pro|Polo|Nintendo\s+Switch|Switch|PS\d+|PC|Epson|EcoTank|"
    r"Hello\s+Kitty|DUPLO|VDE|Torx|FTP|PTZ|DPI|PPP|RH\s*\d+|UV|RJ\d+|Cat\s?\d+|LED|AAA?|A[345]|"
    r"IP\d+|E\d+|GU\d+|XXS|XS|XL|XXL|XXXL|[A-Z]\s?\d+\s?[A-Z](?:-[A-Z])?|[A-Z]{2,5}-?\d+[A-Z]?|"
    r"\d+(?:[.,]\d+)?(?:mah|ghz|mm|cm|ml|mg|kg|hz|gb|tb|ah|db|[mlgwvadp])(?![A-Za-z]))", re.I,
)
LATIN_WORD = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{2,}")
CJK = re.compile(r"[\u3400-\u9fff]")
REVIEWED_NON_TRANSLATION_DECISIONS = frozenset({
    "CONFIRMED_BRAND_OR_IP", "PRODUCT_SERIES_OR_STYLE",
})


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


@lru_cache(maxsize=16)
def _brand_pattern(brands: tuple[str, ...]) -> re.Pattern[str] | None:
    """Compile all known brands once instead of running one regex per brand."""
    # Longest-first preserves the previous caller contract when one brand is
    # a prefix of another (for example, ``Action`` and ``Action Kids``).
    alternatives = [
        re.escape(brand)
        for brand in sorted({_text(value) for value in brands if _text(value)}, key=len, reverse=True)
    ]
    if not alternatives:
        return None
    return re.compile("(?:" + "|".join(alternatives) + ")", re.IGNORECASE)


def _source_path(cfg: dict, key: str) -> Path:
    raw = (cfg.get("dictionary_sources") or {}).get(key, "")
    path = Path(raw)
    return path if path.is_absolute() else cfg["project_root"] / path


def _residual_latin(value: str, brands: list[str]) -> list[str]:
    pattern = _brand_pattern(tuple(brands))
    cleaned = pattern.sub("", value) if pattern else value
    cleaned = ALLOWED_LATIN.sub("", cleaned)
    return LATIN_WORD.findall(cleaned)


def _candidates(products: list[dict[str, str]], brands: list[str]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for row in products:
        name, spec = row["name_zh_standard"], row["spec_zh_standard"]
        name_bad = (
            row["translation_status"] == "UNTRANSLATED"
            or bool(SPANISH_MARKERS.search(name))
            or bool(_residual_latin(name, brands))
        )
        spec_bad = bool(SPANISH_MARKERS.search(spec)) or bool(_residual_latin(spec, brands))
        if name_bad or spec_bad:
            result.append({
                "sku": row["sku"], "source_hash": row["source_hash"], "name_es": row["name_es_raw"],
                "spec_es": row["spec_es_raw"], "current_name_zh": name, "current_spec_zh": spec,
                "brand": row["brand_id"], "cat1_zh": row["cat1_zh"], "source_last_seen": row["source_last_seen"],
                "name_bad": name_bad, "spec_bad": spec_bad,
            })
    return result


def _reviewed_non_translation_keys(out_dir: Path) -> set[tuple[str, str]]:
    """排除已完成品牌/IP或产品系列审查的残留，不交给模型强行翻译。"""
    files = sorted(
        list(out_dir.glob("brand_review_decisions_*.csv")) + list(out_dir.glob("brand_review_queue.csv")),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not files:
        return set()
    with files[0].open("r", encoding="utf-8-sig", newline="") as fh:
        return {
            (_text(row.get("sku")), _text(row.get("source_hash")))
            for row in csv.DictReader(fh)
            if _text(row.get("decision")) in REVIEWED_NON_TRANSLATION_DECISIONS
            and _text(row.get("sku")) and _text(row.get("source_hash"))
        }


SYSTEM_PROMPT = """你是 Action 西班牙商品的中文主数据审校员。只处理指定字段。
规则：中文标准品名中除明确给出的品牌、合法授权 IP、型号、国际技术缩写和计量单位外，不得保留西班牙语或英语自然语言；品牌默认不进入标准品名。根据西语品名、规格和类目修正中文品名和规格。普通西语/英语必须翻译或删除；DIY 不是技术缩写，按语义译为“手工制作”或“自制”；Pop it 统一译为“按压泡泡”。不能确定商品本体时，不猜测，quality_status 写 NEEDS_REVIEW，中文名写“中文品名待人工核验”。规格统一为中文，数字和单位不留空格，尺寸使用 ×，数量按商品语义写件装/条装/双装等。只返回 JSON：{"items":[{"sku":"","name_zh_standard":"","spec_zh_standard":"","quality_status":"OK|NEEDS_REVIEW","notes":""}]}。"""


def _request(api_key: str, items: list[dict[str, str]]) -> list[dict[str, str]]:
    request_items = [{
        "sku": item["sku"], "brand": item["brand"], "category": item["cat1_zh"],
        "spanish_name": item["name_es"], "spanish_spec": item["spec_es"],
        "current_chinese_name": item["current_name_zh"], "current_chinese_spec": item["current_spec_zh"],
        "needs_name_review": item["name_bad"], "needs_spec_review": item["spec_bad"],
    } for item in items]
    body = json.dumps({
        "model": "deepseek-chat", "temperature": 0.1, "max_tokens": 5000,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "请审校并返回全部项目：\n" + json.dumps({"items": request_items}, ensure_ascii=False)},
        ],
    }, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        "https://api.deepseek.com/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}, method="POST",
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        payload = json.loads(response.read().decode("utf-8"))
    content = _text(payload["choices"][0]["message"].get("content"))
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    returned = json.loads(content).get("items")
    if not isinstance(returned, list) or len(returned) != len(items):
        raise ValueError("MODEL_RESPONSE_INCOMPLETE")
    by_sku = {_text(item.get("sku")): item for item in returned}
    if set(by_sku) != {item["sku"] for item in items}:
        raise ValueError("MODEL_RESPONSE_SKU_MISMATCH")
    return [by_sku[item["sku"]] for item in items]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="仅用于分批验收；0 表示全部候选")
    parser.add_argument("--current-only", action="store_true", help="仅处理最后观察日期等于本轮 CURRENT 日期的 SKU")
    parser.add_argument("--force", action="store_true", help="重新审校现有同源模型结果")
    parser.add_argument("--show", action="store_true", help="输出候选 SKU 与待审校字段，便于人工复核")
    parser.add_argument("--valid-spanish-source", action="store_true", help="仅处理西语品名和规格均未被中文覆盖的 SKU")
    args = parser.parse_args()
    cfg = load_settings()
    out_dir: Path = cfg["paths"]["dictionary"]
    products = load_dictionary_rows(
        out_dir / "product_dictionary.csv",
        headers=[
            "sku", "canonical_id", "name_es_raw", "name_zh_standard", "brand_id", "cat1_es", "cat2_es",
            "cat1_zh", "cat2_zh", "spec_es_raw", "spec_zh_standard", "source_hash", "translation_status",
            "review_status", "locked", "source_first_seen", "source_last_seen", "updated_at", "notes",
        ], key_fields=("sku",),
    )
    brand_rows = load_dictionary_rows(
        out_dir / "brand_dictionary.csv",
        headers=["brand_id", "canonical_name", "aliases_es", "keep_original", "is_action_brand", "confidence", "review_status", "notes"],
        key_fields=("brand_id",),
    )
    brands = sorted({_text(row["canonical_name"]) for row in brand_rows if _text(row["canonical_name"])}, key=len, reverse=True)
    candidates = _candidates(products, brands)
    if args.current_only:
        current_date = max((row["source_last_seen"] for row in products if row["source_last_seen"]), default="")
        candidates = [item for item in candidates if item["source_last_seen"] == current_date]
    if args.valid_spanish_source:
        candidates = [
            item for item in candidates
            if not CJK.search(item["name_es"] + item["spec_es"])
        ]
    reviewed_exclusions = _reviewed_non_translation_keys(out_dir)
    before_review_exclusion = len(candidates)
    candidates = [
        item for item in candidates
        if (item["sku"], item["source_hash"]) not in reviewed_exclusions
    ]
    if args.limit:
        candidates = candidates[:args.limit]
    print(json.dumps({
        "candidates": len(candidates), "brands": len(brands), "dry_run": args.dry_run,
        "reviewed_non_translation_excluded": before_review_exclusion - len(candidates),
    }, ensure_ascii=False))
    if args.show:
        for item in candidates:
            print(json.dumps(item, ensure_ascii=False))
    if args.dry_run or not candidates:
        return 0
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY_MISSING")
    output_path = out_dir / "model_translation_overrides.csv"
    existing = {
        row["sku"]: row for row in load_dictionary_rows(
            output_path, headers=MODEL_TRANSLATION_HEADERS, key_fields=("sku",),
        )
    }
    pending = candidates if args.force else [
        item for item in candidates
        if existing.get(item["sku"], {}).get("source_hash") != item["source_hash"]
    ]
    for index in range(0, len(pending), 8):
        batch = pending[index:index + 8]
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                returned = _request(api_key, batch)
                for item, translated in zip(batch, returned):
                    existing[item["sku"]] = {
                        "sku": item["sku"], "source_hash": item["source_hash"],
                        "name_zh_standard": _text(translated.get("name_zh_standard")) or item["current_name_zh"],
                        "spec_zh_standard": _text(translated.get("spec_zh_standard")) or item["current_spec_zh"],
                        "quality_status": _text(translated.get("quality_status")) or "NEEDS_REVIEW",
                        "model": "deepseek-chat", "updated_at": datetime.now().date().isoformat(),
                        "notes": _text(translated.get("notes")),
                    }
                write_dictionary_csv(output_path, [existing[key] for key in sorted(existing)], MODEL_TRANSLATION_HEADERS, key_fields=("sku",))
                print(f"completed {min(index + len(batch), len(pending))}/{len(pending)}")
                break
            except (urllib.error.URLError, urllib.error.HTTPError, ValueError, KeyError) as exc:
                last_error = exc
                time.sleep(attempt * 2)
        else:
            raise RuntimeError(f"TRANSLATION_BATCH_FAILED at {index}: {last_error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
