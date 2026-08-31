from __future__ import annotations

import re
from typing import Any, Mapping

from .contracts import SemanticFact, SourceFacts

_TERM_MAP = {
    "gomas": ("PRODUCT_TYPE", "橡皮筋"), "barra de cola": ("PRODUCT_TYPE", "胶棒"),
    "alfombrilla para cortar": ("PRODUCT_TYPE", "切割垫"), "paño": ("PRODUCT_TYPE", "清洁布"),
    "paños": ("PRODUCT_TYPE", "清洁布"), "detergente": ("PRODUCT_TYPE", "洗洁精"),
    "barritas para gato": ("PRODUCT_TYPE", "猫零食条"), "barritas para gatos": ("PRODUCT_TYPE", "猫零食条"),
    "auriculares": ("PRODUCT_TYPE", "耳机"), "cartulina": ("PRODUCT_TYPE", "彩色手工卡纸"),
    "cola para madera": ("PRODUCT_TYPE", "木工胶"), "gofres": ("PRODUCT_TYPE", "华夫饼"),
    "iluminación": ("PRODUCT_TYPE", "照明灯"), "cápsulas": ("PRODUCT_TYPE", "胶囊"),
    "calcetines": ("PRODUCT_TYPE", "袜子"), "manoplas": ("PRODUCT_TYPE", "沐浴手套"),
    "lámpara": ("PRODUCT_TYPE", "灯"), "concentrador": ("PRODUCT_TYPE", "集线器"),
    "conmutador": ("PRODUCT_TYPE", "交换机"), "speculoos": ("PRODUCT_TYPE", "焦糖饼干"),
}
_COLORS = {"blanco": "白色", "blanca": "白色", "negro": "黑色", "negra": "黑色", "rojo": "红色", "roja": "红色", "verde": "绿色", "azul": "蓝色", "multicolor": "彩色", "antracita": "炭灰色"}
_DETAIL_KEYS = {"color": "颜色", "cantidad": "数量", "contenido": "含量", "material": "材质", "número de producto": "商品编号", "numero de producto": "商品编号", "tamaño": "尺寸", "peso": "重量", "potencia": "功率", "voltaje": "电压", "tipo": "类型"}
_SEMANTIC_PATTERNS = (
    ("SIZE_DIMENSION", r"\b\d+(?:[.,]\d+)?\s*[xX×]\s*\d+(?:[.,]\d+)?\s*(?:cm|mm|m)\b"),
    ("CAPACITY", r"\b\d+(?:[.,]\d+)?\s*(?:ml|l|litros?)\b"),
    ("WEIGHT", r"\b\d+(?:[.,]\d+)?\s*(?:g|gramos?|kg|kilos?)\b"),
    ("QUANTITY", r"\b\d+\s*(?:unidades?|piezas?|pares?|uds?\.?|packs?)\b"),
    ("VOLTAGE", r"\b\d+(?:[–-]\d+)?\s*V\b"),
    ("POWER", r"\b\d+(?:[.,]\d+)?\s*W\b"),
    ("BATTERY_CAPACITY", r"\b\d+(?:[.,]\d+)?\s*mAh\b"),
    ("PROTECTION_RATING", r"\bIP\d{2}\b"),
    ("SOCKET", r"\bE\d{2}\b"),
    ("INTERFACE", r"\b(?:USB-[A-Z]|HDMI|Bluetooth|Wi-?Fi)\b"),
    ("MATERIAL", r"\b(?:algodón|poliéster|nylon|plástico|madera|acero|aluminio|microfibra|caucho)\b"),
    ("COMPATIBILITY", r"\b(?:compatible|compatibilidad|para\s+(?:HP|Epson|iPhone|Android))\b"),
    ("NUTRITION", r"\b(?:vitamina|omega[- ]?3|colágeno|magnesio|proteína)\b"),
)


def parse_semantic_facts(source: SourceFacts, *, known_brands: set[str] | None = None, dictionaries: Mapping[str, Any] | None = None) -> tuple[SemanticFact, ...]:
    dictionaries = dictionaries or {}
    text_fields = (("name_es", source.name_es), ("spec_es", source.spec_es), ("desc_es", source.desc_es), ("details_es", source.details_es))
    facts: list[SemanticFact] = []
    seen: set[tuple[str, str]] = set()
    # Versioned dictionaries are preferred over the small deterministic seed
    # map.  Aliases are matched as phrases, never as arbitrary substrings of
    # an unrelated word.
    product_rows = dictionaries.get("product_type_rows") or dictionaries.get("product_types") or ()
    phrase_rows = dictionaries.get("phrases") or ()
    detail_rows = dictionaries.get("detail_keys") or ()
    tech_rows = dictionaries.get("tech_tokens") or ()
    if isinstance(product_rows, Mapping):
        product_rows = [{"source_term": key, "canonical_zh": value} for key, value in product_rows.items()]
    if isinstance(tech_rows, Mapping):
        tech_rows = [{"token": key, "canonical_token": value, "token_type": "TECH_TOKEN"} for key, value in tech_rows.items()]
    def add(kind: str, source_text: str, zh: str, field: str, evidence: str, *, canonical: str | None = None) -> None:
        key = (kind, source_text.casefold(), zh)
        if key in seen or not source_text or not zh:
            return
        facts.append(SemanticFact(kind, source_text, zh, canonical or zh, field, evidence, 1.0, "", source.source_hash))
        seen.add(key)
    for field, text in text_fields:
        lower = text.lower()
        for row in product_rows if isinstance(product_rows, (list, tuple)) else ():
            term = str(row.get("source_term") or "").strip()
            aliases = [term, *re.split(r"\s*[|,;]\s*", str(row.get("source_aliases") or ""))]
            zh = str(row.get("canonical_zh") or "").strip()
            if zh and any(alias and re.search(rf"(?<!\w){re.escape(alias.casefold())}(?!\w)", lower) for alias in aliases):
                add("PRODUCT_TYPE", term or aliases[0], zh, field, "product_type_dictionary")
        for row in phrase_rows if isinstance(phrase_rows, (list, tuple)) else ():
            phrase = str(row.get("source_phrase") or "").strip()
            zh = str(row.get("zh_value") or "").strip()
            kind = str(row.get("semantic_type") or "DESCRIPTION_FACT").strip().upper()
            if phrase and zh and phrase.casefold() in lower:
                add(kind if kind in {"VARIANT", "FUNCTION", "CARE", "DESCRIPTION_FACT", "MATERIAL", "COMPATIBILITY"} else "DESCRIPTION_FACT", phrase, zh, field, "phrase_dictionary")
        for key_es, key_zh in _DETAIL_KEYS.items():
            if re.search(rf"(?:^|[;|\n])\s*{re.escape(key_es)}\s*[:：]", lower):
                add("DETAIL_KEY", key_es, key_zh, field, key_es)
        for row in detail_rows if isinstance(detail_rows, (list, tuple)) else ():
            key_es = str(row.get("key_es") or "").strip(); key_zh = str(row.get("key_zh") or "").strip()
            if key_es and key_zh and re.search(rf"(?:^|[;|\n])\s*{re.escape(key_es)}\s*[:：]", lower):
                add("DETAIL_KEY", key_es, key_zh, field, "detail_key_dictionary")
        for term, (kind, zh) in _TERM_MAP.items():
            if term in lower and (kind, zh) not in seen:
                add(kind, term, zh, field, term)
        for token, zh in _COLORS.items():
            if re.search(rf"\b{re.escape(token)}\b", lower) and ("COLOR", zh) not in seen:
                add("COLOR", token, zh, field, token)
        for match in re.finditer(r"\b\d+(?:[.,]\d+)?\s?(?:mg|mcg|mAh|ml|l|g|kg|cm|mm|V|W|pulgadas?|unidades?|piezas?|pares?|denier)\b", text, re.I):
            raw = match.group(0).replace(" ", "")
            add("STANDARD_UNIT", raw, raw, field, match.group(0))
        for row in tech_rows if isinstance(tech_rows, (list, tuple)) else ():
            token = str(row.get("token") or "").strip(); canonical = str(row.get("canonical_token") or token).strip()
            if token and re.search(rf"(?<!\w){re.escape(token)}(?!\w)", text, re.I):
                add(str(row.get("token_type") or "TECH_TOKEN").upper(), token, canonical, field, "tech_token_dictionary", canonical=canonical)
        for match in re.finditer(r"\b(?:USB-[A-Z]|A\d+|D\d+|E\d+|[A-Z]{1,4}\d{2,}[A-Z0-9-]*)\b", text):
            token = match.group(0)
            kind = "TECH_TOKEN" if token.upper().startswith(("USB", "E")) or token[0].isalpha() else "MODEL"
            add(kind, token, token, field, token)
        for kind, pattern in _SEMANTIC_PATTERNS:
            for match in re.finditer(pattern, text, re.I):
                raw = match.group(0)
                # Keep the source token as value; the formatter/planner owns
                # Chinese rendering and therefore cannot silently lose facts.
                if (kind, raw.lower()) not in seen:
                    add(kind, raw, raw, field, raw)
    for brand in known_brands or set():
        if brand and brand.lower() in source.name_es.lower():
            add("BRAND", brand, brand, "name_es", brand)
    return tuple(facts)
