from __future__ import annotations

import re

_DETAIL_KEY_MAP = {
    "color": "颜色", "cantidad": "数量", "contenido": "含量", "material": "材质",
    "número de producto": "商品编号", "numero de producto": "商品编号", "número del artículo": "商品编号", "numero del articulo": "商品编号", "tipo": "类型",
    "tamaño": "尺寸", "peso": "重量", "potencia": "功率", "voltaje": "电压",
    "incluye": "包含", "sin alcohol": "含酒精", "sin cafeína": "无咖啡因",
    "instrucciones de lavado": "洗涤说明", "instrucciones de planchado": "熨烫说明",
    "instrucciones de secado": "干燥说明", "uso previsto": "用途",
    "tipo de medio de limpieza / cuidado": "清洁/护理介质类型",
    "protección": "防护等级", "tipo de embalaje": "包装类型",
}
_DETAIL_VALUE_MAP = {
    "azul": "蓝色", "rojo": "红色", "roja": "红色", "negro": "黑色", "negra": "黑色",
    "blanco": "白色", "blanca": "白色", "verde": "绿色", "amarillo": "黄色",
    "sí": "是", "si": "是", "no": "否", "sin planchado": "不可熨烫",
    "uso general": "通用", "paño": "清洁布", "poliamida": "锦纶", "poliéster": "涤纶",
    "lavado a máquina": "可机洗", "apto para secar a baja temperatura": "可低温烘干",
}


def format_spec(value: str) -> str:
    value = str(value or "").strip()
    value = value.replace("|", "｜").replace("×", "×")
    value = re.sub(r"(?<=\d)\s*[xX]\s*(?=\d)", "×", value)
    value = re.sub(r"(?<=\d)\s*[-–]\s*(?=\d)", "–", value)
    value = re.sub(r"(?<=\d),(?=\d)", ".", value)
    value = re.sub(r"\bvarios colores\b|\bvarias colores\b|\bdiferentes colores\b", "多种颜色", value, flags=re.I)
    value = re.sub(r"\bvarias variantes\b|\bdiferentes variantes\b", "多款可选", value, flags=re.I)
    value = re.sub(r"\b(\d+)\s*en\s*(\d+)\b", lambda m: f"{m.group(1)}合{m.group(2)}", value, flags=re.I)
    value = re.sub(r"\bvarios modelos\b|\bvarios modelos\b", "多款可选", value, flags=re.I)
    value = re.sub(r"\bunidades?\b", "件", value, flags=re.I)
    value = re.sub(r"\bpiezas?\b", "件", value, flags=re.I)
    value = re.sub(r"\bpares?\b", "双", value, flags=re.I)
    value = re.sub(r"\bgramos?\b", "g", value, flags=re.I)
    value = re.sub(r"\bkilogramos?\b|\bkilos?\b", "kg", value, flags=re.I)
    value = re.sub(r"\bmetros?\b", "m", value, flags=re.I)
    value = re.sub(r"\bcentímetros?\b", "cm", value, flags=re.I)
    value = re.sub(r"\bmilímetros?\b", "mm", value, flags=re.I)
    value = re.sub(r"\bmiligramos?\b", "mg", value, flags=re.I)
    value = re.sub(r"\bmicrogramos?\b", "mcg", value, flags=re.I)
    value = re.sub(r"\blitros?\b", "L", value, flags=re.I)
    value = re.sub(r"\bmililitros?\b", "ml", value, flags=re.I)
    value = re.sub(r"\bpulgadas?\b", "英寸", value, flags=re.I)
    value = re.sub(r"\bhojas?\b", "张", value, flags=re.I)
    # Unit conversion above may introduce a space (``100 gramos`` -> ``100 g``).
    # Compact retail notation removes only the space immediately before a
    # recognized unit, never spaces between ordinary words.
    value = re.sub(r"(?<=\d)\s+(?=(?:mm|cm|m|ml|l|g|kg|mg|mcg|mAh|V|W|Hz|D|lm|°C|m²)\b)", "", value, flags=re.I)
    value = re.sub(r"\s*｜\s*", "｜", value)
    return value.strip(" ｜")


def format_unit_price(value: str) -> str:
    value = str(value or "").strip()
    value = re.sub(r"€/kg", "€/千克", value, flags=re.I)
    value = re.sub(r"€/l", "€/升", value, flags=re.I)
    value = re.sub(r"€/ud\.?", "€/件", value, flags=re.I)
    return value


def format_text(value: str) -> str:
    text = str(value or "").replace(";", "；").replace(":", "：")
    # A small audited phrase seed keeps deterministic descriptions useful;
    # unknown prose remains visible and is sent to Review/AI rather than
    # being guessed here.
    phrase_map = {
        "varios colores": "多种颜色", "diferentes variantes": "多款可选",
        "apto para lavavajillas": "可用洗碗机清洗", "sin alcohol": "不含酒精",
        "sin cafeína": "不含咖啡因", "uso general": "通用",
    }
    for source, target in phrase_map.items():
        text = re.sub(rf"(?i)\b{re.escape(source)}\b", target, text)
    return re.sub(r"\s+", " ", text).strip()


def format_details(value: str) -> str:
    """Normalize common Spanish detail keys/boolean values conservatively."""
    text = str(value or "").strip()
    if not text:
        return ""
    for source, target in sorted(_DETAIL_KEY_MAP.items(), key=lambda item: len(item[0]), reverse=True):
        text = re.sub(rf"(?i)(?<!\w){re.escape(source)}\s*[:：]", target + "：", text)
    text = re.sub(r"(?i)(?<=[:：])\s*Sí\b", " 是", text)
    text = re.sub(r"(?i)(?<=[:：])\s*No\b", " 否", text)
    for source, target in _DETAIL_VALUE_MAP.items():
        text = re.sub(rf"(?i)(?<!\w){re.escape(source)}(?!\w)", target, text)
    text = re.sub(r"(?i)\b(unidades?|piezas?)\b", "件", text)
    text = re.sub(r"(?i)\bpares?\b", "双", text)
    text = re.sub(r"(?i)\bhojas?\b", "张", text)
    text = re.sub(r"(?i)\bgramos?\b", "g", text)
    text = re.sub(r"(?i)\blitros?\b", "L", text)
    text = re.sub(r"(?<=\d)\s+(?=(?:件|双|张|g|L|ml|kg|mg|mcg|mAh|V|W|°C|cm|mm|m|lm)\b)", "", text, flags=re.I)
    text = text.replace("\n", "；").replace(";", "；")
    text = re.sub(r"\s*；\s*", "；", text)
    text = re.sub(r"\s*：\s*", "：", text)
    return text.strip("； ")
