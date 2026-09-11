"""Translate and standardize the current Chinese export without mutating Master.

The script deliberately writes a reviewable export artifact only.  Official
Spanish facts remain the source of truth; existing trusted Chinese values are
kept, and only blank/Spanish fallback fields are sent to the configured model.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

PROJECT = Path(__file__).resolve().parents[1]
SRC = PROJECT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from action_tracker.config import load_settings
from action_tracker.database.integration import database_path
from action_tracker.database.repository import ProductionRepository
from action_tracker.exporting.dictionary_join import build_zh_rows, load_dictionary_context
from action_tracker.exporting.excel_writer import write_catalog_xlsx


FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")
FIELD_TO_ROW = {
    "name": "标题", "cat1": "分类1", "cat2": "分类2", "spec": "规格",
    "description": "描述", "details": "产品详情",
}
FIELD_TO_ES = {
    "name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es", "spec": "spec_es",
    "description": "desc_es", "details": "details_es",
}
SPANISH = re.compile(
    r"\b(?:el|la|los|las|de|del|con|para|sin|una|un|unos|unas|varios|varias|diferentes|"
    r"talla|tallas|unidades?|piezas?|gramos?|metros?|litros?|paquete|juego|calcetines|"
    r"mallas|pantis|guantes|gorro|chocolate|cable|bolsa|discos|gel|colores?|hogar|"
    r"oficina|papelería|mascotas|juguetes|manualidades|limpieza|deporte)\b", re.I,
)
CJK = re.compile(r"[\u3400-\u9fff]")

SYSTEM = """你是 Action 西班牙站商品中文主数据终审员。只处理给出的六个中文字段，不能改 SKU、价格、链接或任何西语事实。
必须遵守两份规范：身份/商品本体放品名；消费者选购参数放规格；卖点、用途、使用说明放描述；结构化参数、护理、营养、认证放产品详情。
品名、分类、规格、描述、详情中的普通西语必须翻译成简洁自然的中文。允许保留品牌、系列/IP、型号、接口、技术标准、认证和计量单位等合法拉丁字符，但不要把普通西语当品牌，也不要臆造品牌/功能。
分类1只能使用这15个固定值：DIY五金、办公文具、宠物用品、厨房餐具、服饰鞋包、个人护理、家居清洁用品、家居/日用、家居/收纳、家居/浴室用品、家居/装饰、旅行用品、派对用品、食品/饮料、数码/电子、体育用品、玩具、文具/办公、文具/手作/礼品、园艺/户外、运动用品。若输入已是规范值，原样保留。分类2翻译为简短中文类目，不要留普通西语。
保持数字、型号、单位、颜色、数量、规格事实；尺寸用×，字段内多项用“｜”，数量量词按商品语义。描述逐句翻译，不凭品名补写；产品详情保留原有键值结构，键和值都翻译，商品编号和数字不变；源字段为空则输出空字符串。
只返回 JSON：{"items":[{"sku":"...","name":"...","cat1":"...","cat2":"...","spec":"...","description":"...","details":"...","notes":"..."}]}。必须返回输入中的全部 SKU，不能遗漏。"""


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _hash_rows(rows: list[dict[str, Any]]) -> str:
    raw = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _needs_model(field: str, current: str, source: str, baseline: str) -> bool:
    # Empty source is a valid empty field; do not invent text.
    if not source:
        return False
    current = _text(current)
    if not current or current == source:
        return True
    if field in {"cat1", "cat2", "name", "spec"} and not CJK.search(current):
        return True
    if field in {"description", "details"} and (not CJK.search(current) or SPANISH.search(current)):
        return True
    return bool(SPANISH.search(current))


def _parse_model_items(content: str, batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
    content = _text(content)
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    items = json.loads(content).get("items")
    if not isinstance(items, list):
        raise ValueError("MODEL_RESPONSE_ITEMS_MISSING")
    if not all(isinstance(row, dict) for row in items):
        raise ValueError("MODEL_RESPONSE_ITEM_NOT_OBJECT")
    expected = {_text(row.get("sku")) for row in batch}
    returned = {_text(row.get("sku")) for row in items}
    if returned != expected:
        raise ValueError(f"MODEL_RESPONSE_SKU_MISMATCH expected={len(expected)} returned={len(returned)}")
    return items


def _request_deepseek(api_key: str, batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload = {
        "model": "deepseek-chat", "temperature": 0.1, "max_tokens": 12000,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": "请逐条翻译并优化以下候选记录，只返回 JSON：\n" + json.dumps({"items": batch}, ensure_ascii=False)},
        ],
    }
    request = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        body = json.loads(response.read().decode("utf-8"))
    return _parse_model_items(body["choices"][0]["message"].get("content"), batch)


def _request_ollama(url: str, batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload = {
        "model": "qwen3:8b", "stream": False,
        "format": "json", "options": {"temperature": 0.1, "num_ctx": 8192, "num_predict": 2200},
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": "请逐条翻译并优化以下候选记录，只返回 JSON：\n" + json.dumps({"items": batch}, ensure_ascii=False)},
        ],
    }
    request = urllib.request.Request(
        url.rstrip("/") + "/api/chat", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        body = json.loads(response.read().decode("utf-8"))
    return _parse_model_items(body["message"].get("content"), batch)


def _safe_model_value(value: Any, source: str, current: str) -> str:
    value = _text(value)
    # An empty answer is never allowed to erase an existing trusted value.
    return value or _text(current) or ("" if not source else _text(source))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--provider", choices=("deepseek", "ollama"), default="deepseek")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11500")
    args = parser.parse_args()
    cfg = load_settings()
    repo = ProductionRepository(database_path(cfg))
    records = repo.load_current_export_records()
    dictionary = load_dictionary_context(cfg)
    baseline_rows, baseline_fallbacks = build_zh_rows(records, dictionary)
    by_sku = {_text(row["编号"]): row for row in baseline_rows}
    record_by_sku = {_text(row["sku"]): row for row in records}

    candidates: list[dict[str, Any]] = []
    for sku in sorted(record_by_sku, key=lambda x: (int(x) if x.isdigit() else 10**18, x)):
        record = record_by_sku[sku]
        base = by_sku[sku]
        needed: list[str] = []
        for field in FIELDS:
            current = _text(base[FIELD_TO_ROW[field]])
            source = _text(record.get(FIELD_TO_ES[field]))
            if _needs_model(field, current, source, current):
                needed.append(field)
        if needed:
            candidates.append({
                "sku": sku, "needed": needed,
                "name_es": _text(record.get("name_es")), "cat1_es": _text(record.get("cat1_es")),
                "cat2_es": _text(record.get("cat2_es")), "spec_es": _text(record.get("spec_es")),
                "description_es": _text(record.get("desc_es")), "details_es": _text(record.get("details_es")),
                "name_current": _text(base["标题"]), "cat1_current": _text(base["分类1"]),
                "cat2_current": _text(base["分类2"]), "spec_current": _text(base["规格"]),
                "description_current": _text(record.get("desc_zh")), "details_current": _text(record.get("details_zh")),
            })
    if args.limit:
        candidates = candidates[:args.limit]
    cache_dir = Path(cfg["paths"]["temp"]) / "chinese_model_translation"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{args.date.replace('-', '')}.json"
    cache: dict[str, dict[str, Any]] = {}
    if args.resume and cache_path.exists():
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    pending = [row for row in candidates if row["sku"] not in cache]
    print(json.dumps({"current_records": len(records), "candidates": len(candidates), "pending": len(pending), "baseline_fallbacks": baseline_fallbacks}, ensure_ascii=False))
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if pending and args.provider == "deepseek" and not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY_MISSING")
    for offset in range(0, len(pending), max(1, args.batch_size)):
        batch = pending[offset:offset + max(1, args.batch_size)]
        request_rows = []
        for row in batch:
            request_rows.append({
                "sku": row["sku"], "needed_fields": row["needed"],
                "source": {k[:-3] if k.endswith("_es") else k: row[k] for k in ("name_es", "cat1_es", "cat2_es", "spec_es", "description_es", "details_es")},
                "current": {k[:-8] if k.endswith("_current") else k: row[k] for k in ("name_current", "cat1_current", "cat2_current", "spec_current", "description_current", "details_current")},
            })
        last: Exception | None = None
        for attempt in range(1, 4):
            try:
                result = (_request_ollama(args.ollama_url, request_rows)
                          if args.provider == "ollama" else _request_deepseek(api_key, request_rows))
                for item in result:
                    cache[_text(item["sku"])] = item
                cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"translated {min(offset + len(batch), len(pending))}/{len(pending)}")
                break
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, KeyError, TypeError, AttributeError, json.JSONDecodeError) as exc:
                last = exc
                time.sleep(attempt * 2)
        else:
            raise RuntimeError(f"TRANSLATION_BATCH_FAILED offset={offset}: {last}")

    final_rows: list[dict[str, Any]] = []
    for sku in sorted(record_by_sku, key=lambda x: (int(x) if x.isdigit() else 10**18, x)):
        record = record_by_sku[sku]
        base = dict(by_sku[sku])
        model = cache.get(sku, {})
        for field in FIELDS:
            if field not in next((x["needed"] for x in candidates if x["sku"] == sku), []):
                continue
            target = FIELD_TO_ROW[field]
            source = _text(record.get(FIELD_TO_ES[field]))
            current = _text(base[target])
            base[target] = _safe_model_value(model.get(field), source, current)
        # Empty official source remains empty; never fabricate description/details.
        if not _text(record.get("desc_es")):
            base["描述"] = ""
        if not _text(record.get("details_es")):
            base["产品详情"] = ""
        final_rows.append(base)

    output_dir = Path(cfg["paths"]["exports"])
    output_dir.mkdir(parents=True, exist_ok=True)
    date_compact = args.date.replace("-", "")
    output = output_dir / f"{date_compact}Action商品全量_中文版_模型优化版_不带图.xlsx"
    profile = {
        "sheet_name": "商品全量", "freeze_panes": "A2", "auto_filter": True,
        "header": {"bold": True, "fill": "1F4E78", "font_color": "FFFFFF"},
        "body": {"wrap_text_columns": ["标题", "分类1", "分类2", "规格", "描述", "产品详情", "备注"], "max_row_height": 405},
        "price": {"number_format": "€#,##0.00"},
    }
    write_catalog_xlsx(output, headers=["图片", "编号", "标题", "分类1", "分类2", "规格", "折后价", "原价", "单价", "描述", "产品详情", "图片链接", "商品链接", "备注"], rows=final_rows, workbook_format=profile)
    audit = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(), "date": args.date,
        "sku_count": len(final_rows), "candidate_count": len(candidates), "translated_count": len(cache),
        "source": "SQLITE_CURRENT + FILE_DICTIONARY + DEEPSEEK_MODEL",
        "source_hash": _hash_rows([{k: r.get(k) for k in ("编号", "标题", "分类1", "分类2", "规格", "描述", "产品详情")} for r in final_rows]),
        "cache": str(cache_path), "output": str(output),
    }
    output.with_suffix(".manifest.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
