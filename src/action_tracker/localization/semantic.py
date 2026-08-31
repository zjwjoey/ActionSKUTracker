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


def parse_semantic_facts(source: SourceFacts, *, known_brands: set[str] | None = None, dictionaries: Mapping[str, Any] | None = None) -> tuple[SemanticFact, ...]:
    text_fields = (("name_es", source.name_es), ("spec_es", source.spec_es), ("desc_es", source.desc_es), ("details_es", source.details_es))
    facts: list[SemanticFact] = []
    seen: set[tuple[str, str]] = set()
    for field, text in text_fields:
        lower = text.lower()
        for key_es, key_zh in _DETAIL_KEYS.items():
            if re.search(rf"(?:^|[;|\n])\s*{re.escape(key_es)}\s*[:：]", lower):
                facts.append(SemanticFact("DETAIL_KEY", key_es, key_zh, key_zh, field, key_es))
        for term, (kind, zh) in _TERM_MAP.items():
            if term in lower and (kind, zh) not in seen:
                facts.append(SemanticFact(kind, term, zh, zh, field, term))
                seen.add((kind, zh))
        for token, zh in _COLORS.items():
            if re.search(rf"\b{re.escape(token)}\b", lower) and ("COLOR", zh) not in seen:
                facts.append(SemanticFact("COLOR", token, zh, zh, field, token)); seen.add(("COLOR", zh))
        for match in re.finditer(r"\b\d+(?:[.,]\d+)?\s?(?:mg|mcg|mAh|ml|l|g|kg|cm|mm|V|W|pulgadas?|unidades?|piezas?|pares?|denier)\b", text, re.I):
            raw = match.group(0).replace(" ", "")
            facts.append(SemanticFact("STANDARD_UNIT", raw, raw, raw, field, match.group(0)))
        for match in re.finditer(r"\b(?:USB-[A-Z]|A\d+|D\d+|E\d+|[A-Z]{1,4}\d{2,}[A-Z0-9-]*)\b", text):
            token = match.group(0)
            kind = "TECH_TOKEN" if token.upper().startswith(("USB", "E")) or token[0].isalpha() else "MODEL"
            facts.append(SemanticFact(kind, token, token, token, field, token))
    for brand in known_brands or set():
        if brand and brand.lower() in source.name_es.lower():
            facts.append(SemanticFact("BRAND", brand, brand, brand, "name_es", brand))
    return tuple(facts)
