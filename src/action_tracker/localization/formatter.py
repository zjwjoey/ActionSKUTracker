from __future__ import annotations

import re

_DETAIL_KEY_MAP = {
    "color": "颜色", "cantidad": "数量", "contenido": "含量", "material": "材质",
    "número de producto": "商品编号", "numero de producto": "商品编号", "tipo": "类型",
    "tamaño": "尺寸", "peso": "重量", "potencia": "功率", "voltaje": "电压",
    "incluye": "包含", "sin alcohol": "含酒精", "sin cafeína": "无咖啡因",
}
_DETAIL_VALUE_MAP = {"azul": "蓝色", "rojo": "红色", "roja": "红色", "negro": "黑色", "negra": "黑色", "blanco": "白色", "blanca": "白色", "verde": "绿色", "sí": "是", "si": "是", "no": "否"}


def format_spec(value: str) -> str:
    value = str(value or "").strip()
    value = value.replace("|", "｜").replace("×", "×")
    value = re.sub(r"(?<=\d)\s*[xX]\s*(?=\d)", "×", value)
    value = re.sub(r"(?<=\d),(?=\d)", ".", value)
    value = re.sub(r"\bvarios colores\b|\bvarias colores\b|\bdiferentes colores\b", "多种颜色", value, flags=re.I)
    value = re.sub(r"\bvarias variantes\b|\bdiferentes variantes\b", "多款可选", value, flags=re.I)
    value = re.sub(r"\bunidades?\b", "件", value, flags=re.I)
    value = re.sub(r"\bpiezas?\b", "件", value, flags=re.I)
    value = re.sub(r"\bpares?\b", "双", value, flags=re.I)
    value = re.sub(r"\bgramos?\b", "g", value, flags=re.I)
    value = re.sub(r"\blitros?\b", "L", value, flags=re.I)
    value = re.sub(r"\bmililitros?\b", "ml", value, flags=re.I)
    value = re.sub(r"\bpulgadas?\b", "英寸", value, flags=re.I)
    value = re.sub(r"\bhojas?\b", "张", value, flags=re.I)
    # Unit conversion above may introduce a space (``100 gramos`` -> ``100 g``).
    # Compact retail notation removes only the space immediately before a
    # recognized unit, never spaces between ordinary words.
    value = re.sub(r"(?<=\d)\s+(?=(?:mm|cm|m|ml|l|g|kg|mg|mcg|mAh|V|W|Hz|D)\b)", "", value, flags=re.I)
    value = re.sub(r"\s*｜\s*", "｜", value)
    return value.strip(" ｜")


def format_unit_price(value: str) -> str:
    value = str(value or "").strip()
    value = re.sub(r"€/kg", "€/千克", value, flags=re.I)
    value = re.sub(r"€/l", "€/升", value, flags=re.I)
    value = re.sub(r"€/ud\.?", "€/件", value, flags=re.I)
    return value


def format_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace(";", "；").replace(":", "：")).strip()


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
    text = re.sub(r"(?i)\bgramos?\b", "g", text)
    text = re.sub(r"(?i)\blitros?\b", "L", text)
    text = re.sub(r"(?<=\d)\s+(?=(?:件|g|L|ml|kg|mg|mcg|mAh|V|W|°C)\b)", "", text, flags=re.I)
    text = text.replace("\n", "；").replace(";", "；")
    text = re.sub(r"\s*；\s*", "；", text)
    text = re.sub(r"\s*：\s*", "：", text)
    return text.strip("； ")
