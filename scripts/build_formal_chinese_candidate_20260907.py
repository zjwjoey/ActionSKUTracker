"""Build an auditable Chinese localization candidate from the formal ES source.

This is deliberately conservative: protected facts are copied from the Chinese
input, while category joins, safe format normalization, detail-key repair and
explicit source-fact corrections are applied with a cell-level change log.
Unresolved semantic cases remain in review and keep the final gate FAIL.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import openpyxl

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PROJECT = Path(r"F:\ActionSKUTracker")
EXPORTS = PROJECT / "runtime" / "exports"
ES_PATH = EXPORTS / "20260907Action商品全量_西班牙语版_不带图_最终修复版_分类清理版.xlsx"
ZH_PATH = EXPORTS / "20260907Action商品全量_中文版_GPT56Luna最终修复版_不带图.xlsx"
REPORT_PATH = Path(r"D:\Users\Administrator\Downloads\20260907Action中文版_全量规范审查报告.xlsx")
CATEGORY_PATH = PROJECT / "data" / "dictionary" / "category_dictionary.csv"
OUT_PATH = EXPORTS / "20260907Action商品全量_中文版_正式研究候选版_不带图.xlsx"
# Keep the audit name aligned with the delivery contract.  In particular, do
# not derive it with Path.with_suffix(), which would leave the `_不带图` token
# in the audit filename.
AUDIT_PATH = EXPORTS / "20260907Action商品全量_中文版_正式研究候选版.audit.json"
CHANGES_PATH = EXPORTS / "20260907Action商品全量_中文版_正式研究候选版_changes.xlsx"

HEADERS = ["图片", "编号", "标题", "分类1", "分类2", "规格", "折后价", "原价", "单价", "描述", "产品详情", "图片链接", "商品链接", "备注"]
FIELD_BY_HEADER = {"标题": "name", "分类1": "cat1", "分类2": "cat2", "规格": "spec", "描述": "description", "产品详情": "details"}
SOURCE_BY_FIELD = {"name": "标题", "cat1": "分类1", "cat2": "分类2", "spec": "规格", "description": "描述", "details": "产品详情"}

FIXED_CAT1 = {"DIY五金", "办公文具", "宠物用品", "厨房餐具", "服饰鞋包", "个人美容", "家居布置", "家务清洁", "旅行用品", "食品饮料", "数码影音", "玩具", "兴趣手作", "园艺户外", "运动用品"}

# High-confidence source fact repairs explicitly called out by the audit.
P0_DESCRIPTION_FIXES = {
    "2503720": "带防滑手柄和硬质刷毛。采用58%再生塑料制成，适合清洁小面积和边角位置。",
    "2504983": "采用52%再生聚酯纤维制成。不易掉絮，适合清洁镜子、窗户和屏幕，清洁后不易留下痕迹。",
    "2538438": "三合一沐浴露，可用于身体、头发和面部。带清新木质薄荷香味，不含硫酸盐。",
    "2549515": "配超细纤维拖布，带500ml清洁液水箱。加入水和清洁剂后按动手柄即可喷雾清洁地面。",
    "2568745": "550g/m²高克重浴巾，触感柔软亲肤，适合在家中浴室营造舒适的SPA体验。",
    "2572286": "天然奇亚籽，富含维生素、矿物质和膳食纤维。",
    "3009167": "经典款，适合搭配各种服装。多种颜色可选，可用于遮阳或作为日常造型配件。",
    "3204789": "柔软灵活的梳头刷，适合日常使用。采用再生材料制成。",
    "3208366": "适用于所有热源；不粘；黑色大理石纹饰面。",
    "3208380": "适用于所有热源；陶瓷涂层；采用98%再生铝制成；这款大理石纹陶瓷涂层炒锅不易粘锅，适合煎炒蔬菜、肉类或面条，轻松做出理想效果。",
    "3209215": "采用50%再生塑料制成；可叠放；透明设计，清晰可见；带盖收纳盒，可存放夏季或冬季衣物及季节装饰。",
    "3211043": "素食产品；含生物酒精。",
    "3214953": "可充电，配USB-C线（含）；续航播放最长17小时；蓝牙5.4。",
    "3215026": "带橡木盖；缓闭合；FSC认证木材：可持续木材。",
    "3217018": "带纽扣的保暖家居套装；柔软舒适；采用70%再生聚酯纤维制成；柔软舒适的抓绒套装，穿上后舍不得脱下。",
    "3222067": "容量约30L；宽敞购物袋，适合装载购物物品；坚固车架；实用购物车，配坚固车架和宽敞购物袋，便于运输购物物品，无需提起。两个轮子和舒适把手带来良好使用体验。",
    "3222163": "带细腻光泽；配实用涂抹器；纯素产品；这款唇彩可让双唇看起来精致亮泽。",
    "3223337": "底漆和清漆二合一；哑光表面；提供优异防护和遮盖效果；这款金属漆可轻松让金属呈现清新优雅的外观。兼具底漆和清漆功能，可立即开始使用。呈哑光效果并保护表面。",
    "3223967": "带卡纸衬框（30×40cm），呈现奢华效果；百搭优雅设计；哑光表面；为照片或海报提供特别展示位置。这款简约边框设计低调大方，突出图片效果；可放置或悬挂于任意位置；卡纸衬框让照片瞬间呈现优雅整洁效果。",
    "3224147": "奶油味，带辣味点缀；快速方便；柔软口感的方便面零食，适合快速食用。",
}
P0_DETAIL_FIXES = {"3215070": [("是否带提手", "否")]}

SAFE_TEXT_REPLACEMENTS = [
    ("varios colores", "多种颜色"), ("diferentes colores", "多种颜色"),
    ("varias variantes", "多款可选"), ("diferentes variantes", "多款可选"),
    ("unidades", "件"), ("piezas", "件"), ("uds.", "件"),
    ("gramos", "g"), ("gramo", "g"), ("litros", "L"), ("litro", "L"),
    ("mililitros", "ml"), ("metros", "m"), ("metro", "m"),
    ("centímetros", "cm"), ("centímetro", "cm"), ("vatios", "W"), ("vatio", "W"),
    ("voltios", "V"), ("voltio", "V"), ("navideño", "圣诞"), ("navideña", "圣诞"),
    ("dulce", "甜味"), ("Diseño", "图案"), ("Cartón", "纸板"),
    ("Caja de regalo", "礼盒"), ("Cuenco", "碗"), ("Fuente para servir", "餐盘"),
    ("Redondo", "圆形"), ("Navidad", "圣诞"), ("Amarillo", "黄色"),
    ("Blanco cálido", "暖白色"), ("Hierro", "铁"), ("Cobre", "铜"),
    ("Blanco", "白色"), ("Negro", "黑色"), ("Verde", "绿色"), ("Marrón", "棕色"),
    ("Interior", "室内"), ("Plástico", "塑料"), ("Madera", "木材"),
    ("Claro", "浅色"), ("transparente", "透明"),
    ("Puesta a tierra", "接地"), ("Alimentación de red", "电源供电"),
    ("Decoración de temporada", "季节装饰"), ("Lámpara LED", "LED灯"),
    ("Invierno", "冬季"), ("Mujer", "女性"), ("Hombre", "男性"),
    ("Unisex", "男女通用"), ("Shorts tipo bóxer", "平角内裤"),
    ("Piel seca", "干性皮肤"), ("Loción", "乳液"), ("Botella exprimible", "挤压瓶"),
    ("Dedos de los pies cerrados/parte trasera cerrada/entrada abierta", "包头/包后跟/开口"),
    ("Dedos de los pies cerrados/parte trasera abierta", "包头/后跟开口"),
    ("Deslizante sin elástico", "无松紧滑穿"), ("Deslizante con elástico", "带松紧滑穿"),
    ("Sin cierre", "无闭合"), ("Apto para secar a baja temperatura", "适合低温烘干"),
    ("No secar en tambor de secado", "不可滚筒烘干"), ("Tejido de baño", "浴巾面料"),
    ("Toalla de manos", "擦手巾"), ("Toalla de mano de baño", "浴室擦手巾"),
    ("Toalla de mano", "擦手巾"), ("Relacionado con la estación", "季节主题"),
    ("Cuentagotas", "滴管"), ("Aceite", "油"), ("Todos los colores del cabello", "所有发色"),
    ("Canela", "肉桂"), ("Cubierta dura", "硬壳"), ("Con líneas", "带横线"),
    ("Cinta", "丝带"), ("Lavado a mano", "手洗"), ("Sin planchado", "不可熨烫"),
    ("Sintético", "合成纤维"), ("Cojines en forma rectangular", "矩形靠垫"),
    ("Interruptor de presión", "按扣"), ("Arroz", "米"), ("vegetariana", "素食"),
    ("Impermeable", "防水"), ("Plegable", "可折叠"), ("Extraíble", "可拆卸"),
    ("Sí, con varios tipos", "是，多种类型"), ("con varios tipos", "多种类型"),
    ("Espray", "喷雾"), ("Botella", "瓶"), ("Bomba", "泵头"), ("Bote", "罐"),
    ("Cesta de almacenaje", "收纳篮"), ("Medias", "袜子"),
    ("Ver declaración de conformidad", "查看符合性声明"),
    ("Producto para la limpieza／refrescante de WC", "洁厕/清新产品"),
    ("Rodillo de pintura", "油漆滚筒"), ("Acero inoxidable", "不锈钢"),
    ("Sartén", "平底锅"), ("en la pared", "墙面安装"),
    ("Cierre", "闭合"), ("USB-c", "USB-C"),
    ("Blíster", "泡罩包装"), ("Completamente cerrado", "完全闭合"),
    ("Estampado animal", "动物图案"), ("Goma", "橡胶"),
    ("Limpiador - uso general", "通用清洁剂"), ("Rostro", "面部"),
    ("Largo hasta la pantorrilla", "小腿长度"), ("Brocha de pintura", "油漆刷"),
    ("Caja de almacenaje de alimentos", "食品收纳盒"), ("cierre de velcro", "魔术贴闭合"),
    ("Tubo", "管"), ("Saquito", "小袋"), ("Papelera", "垃圾桶"),
    ("PVC", "PVC"), ("A partir de", "适用年龄"),
    ("Sí", "是"), ("No", "否"),
]
SAFE_TEXT_REPLACEMENTS.sort(key=lambda item: len(item[0]), reverse=True)

DETAIL_KEY_MAP = {
    "Color": "颜色", "Material": "材质", "Cantidad": "数量", "Contenido": "含量",
    "Capacidad": "容量", "Potencia": "功率", "Voltaje": "电压", "Número del artículo": "商品编号",
    "Número de artículo": "商品编号", "Instrucciones de lavado": "洗涤说明", "Instrucciones de secado": "干燥说明",
    "Instrucciones de planchado": "熨烫说明", "Uso previsto": "用途", "Destinado a": "适用场景",
    "Pilas incluidas": "含电池", "Recargable": "可充电", "Con tapa": "带盖", "Con mango": "带手柄",
    "Con mangos": "是否带提手", "Con ruedas giratorias": "是否带万向轮", "Con ruedas": "带轮子",
    "Forma": "形状", "Tema": "主题", "Sabor": "口味", "Género": "适用性别", "Edad adecuada": "适用年龄",
    "Talla de las prendas de vestir": "服装尺码", "Talla de calzado": "鞋码", "Longitud del cable": "线缆长度",
    "Longitud de la manga": "袖长", "Medidas (incl. envase) (largo x ancho x alto)": "尺寸（含包装，长×宽×高）",
    "Tipo de caja / cesta": "箱/篮类型", "Tipo de caja de almacenaje de alimentos / bebida": "食品/饮料储物盒类型",
    "Tipo de planta": "植物类型", "Tipo de vela": "蜡烛类型", "Tipo de decoración de temporada": "季节装饰类型",
    "Apto para el lavavajillas": "可用洗碗机清洗", "Apto para el microondas": "可用于微波炉",
    "Resistente al horno": "可耐烤箱", "Sin perfume": "无香味", "Sin alcohol": "不含酒精",
    "Número de hojas": "页数", "Número de páginas": "页数", "Relleno de página": "页面填充",
    "Formato del papel": "纸张规格", "Tipo de conexión": "连接类型", "Tipo de alimentación": "供电方式",
    "Tipo de fuente de luz": "光源类型", "Incluye mando a distancia": "含遥控器", "Incluye oído": "带把手",
    "Incluye tapa abatible": "含翻盖", "Incluye fuente de luz": "是否含光源",
    "Número de enchufes": "插座数量", "Número de botones": "按钮数量",
    "A prueba de salpicaduras": "防溅水", "Resistente a la intemperie": "耐候",
    "Con interruptor de encendido y apagado": "带电源开关", "Con reloj despertador incorporado": "带内置闹钟",
    "Tipo de toma de corriente / enchufe / toma de pared": "插座/插头/墙壁插座类型",
    "Toma de corriente múltiple": "多孔插座", "Protección para niños": "儿童防护", "Tipo de tierra": "接地类型",
    "Incluye sonido": "含声音", "Incluye maceta": "含花盆", "Material de la maceta": "花盆材质",
    "Color suave": "灯光颜色", "Con iluminación integrada": "带内置照明", "Lumen": "流明",
    "Número de horas de combustión": "燃烧时长", "Perfumado": "有香味", "Sustancia": "形态",
    "Tipo de detergente de limpieza de WC": "洁厕剂类型", "Tipo de dispensador": "分配器类型",
    "Función de limpieza": "清洁功能", "Listo para su uso": "可直接使用", "Rellenable": "可重新填充",
    "Incluye indicador de temperatura": "带温度指示器", "Número de compartimentos": "隔层数量",
    "Tipo de espejo": "镜子类型", "Tipo de marco": "镜框类型", "Tipo de planta de plástico": "塑料植物类型",
    "A base de agua": "水性", "Acabado": "表面处理", "Consistencia": "质地",
    "Material de la superficie del objetivo": "目标表面材质", "Se puede pintar después": "可后期涂装",
    "Tipo de pintura": "涂料类型", "Tipo de secado": "干燥类型", "Térmico": "保暖",
    "Sin azúcar": "无糖", "Sin gluten": "无麸质", "Sin granos": "无颗粒", "Sin lactosa": "无乳糖",
    "Vegano": "纯素", "Halal": "清真", "Carbohidratos": "碳水化合物", "Energía": "能量",
    "Grasas": "脂肪", "Proteínas": "蛋白质", "Sal": "盐分", "De los cuales azúcares": "其中糖分",
    "De los cuales saturados": "其中饱和脂肪", "Información sobre alérgenos": "过敏原信息",
    "Consejos sobre conservación": "储存建议", "Salado / dulce": "咸/甜", "Surtidas": "混合装",
    "Instalación": "安装方式", "Método de fijación": "固定方式",
    "Combinación de chocolate / azúcar": "巧克力/糖组合", "Capacidad máxima de carga": "最大承重",
    "Capacidad de carga máxima": "最大承重", "Efecto hidratante": "保湿效果",
    "Material del frasco": "瓶身材质", "Apto para tipo de piel": "适用肤质",
    "Tipo de alimentación para mascotas": "宠物食品类型", "Talla de los guantes": "手套尺码",
    "Con acristalamiento": "带玻璃面", "Apto para varias fotos": "适用于多张照片",
    "Tala medias": "袜子尺码", "Apto para tipo de cabello": "适用发质", "Tipo de medias": "袜子类型",
    "Vegetariana": "素食", "Tipo de perfume": "香水类型", "Número de posiciones": "档位数量",
    "Duración": "持续时间", "Tamaño de la fotografía": "照片尺寸", "Tipo de aplicador de pintura": "涂料涂抹器类型",
    "Tipo de productos para el hogar": "家居用品类型", "Tipo de guantes": "手套类型",
    "Tamaño de la bolsa de basura": "垃圾袋尺寸", "Declaración de conformidad": "符合性声明",
    "Tipo de galleta": "饼干类型", "Versión bluetooth": "蓝牙版本", "Lista de ingredientes": "配料表",
    "Función": "功能", "Tipo de bolsa de basura": "垃圾袋类型", "Impermeable": "防水",
    "Efecto nutritivo": "滋养效果", "Formato de la cama": "床铺规格", "Casquillo de la lámpara": "灯头类型",
    "Conexión de la señal": "信号连接", "Micrófono integrado": "内置麦克风", "Independiente / montado": "独立式/安装式",
    "Número de cuchillas": "刀片数量", "Ancho": "宽度", "Consumo de energía": "能耗",
    "Temperatura de tratamiento": "处理温度", "Incluye cargador": "含充电器", "Antitranspirante": "止汗",
    "Forma de la brocha": "刷子形状", "Incluye soporte": "含支架", "Número de capas": "层数",
    "Forma lámpara": "灯具形状", "Tipo de cubiertos": "餐具类型", "Incluye cuchillos adicionales": "含额外刀具",
    "Tipo de cargador": "充电器类型", "Con adaptador para coche": "含车载适配器", "Compostable": "可堆肥",
    "Contiene flúor": "含氟", "Porción diaria": "每日份量", "Función desengrasante": "去油功能",
    "Tipo de chicle": "口香糖类型", "PH-neutro": "中性pH", "Tipo de champú para el cabello": "洗发水类型",
    "Tipo de cabezal": "刷头类型", "Tipo de compresa higiénica": "卫生巾类型", "Incluye boquilla": "含喷嘴",
    "Manual de usuario": "用户手册", "Antialérgico": "抗过敏", "Control de volumen": "音量控制",
    "Tipo de producto hidratante de cuidado personal": "个人护理保湿产品类型",
    "Tipo de producto para el cuidado bucal": "口腔护理产品类型",
    "Tipo de producto de limpieza facial / desmaquillante": "洁面/卸妆产品类型",
    "Tipos de fuentes de luz": "光源数量", "Material de revestimiento": "涂层材料",
    "Prendas de ropa para mujeres embarazadas": "孕妇装", "Calzado abierto o cerrado": "露趾或闭趾",
    "Forma nariz": "鼻型", "Tipo de cierre": "闭合类型", "Tipo de material superior": "鞋面材质",
    "Tipo de temporada": "季节类型", "Decorado con patrón": "图案装饰", "Denier": "丹尼尔数",
    "Aclarado": "冲洗", "Efecto limpiador": "清洁效果", "Cierre suave": "柔和闭合",
    "Pila alta": "高腰", "Incluye burro": "含支架", "Material estructura": "主体结构材质",
    "Tipo de silla / taburete": "椅子/凳子类型", "Apto para necesidades de la piel": "针对皮肤需求",
    "Sin perfume": "不含香精", "Tipo de dispensador": "分配器类型", "Impermeable / resistente al agua": "防水",
    "Modelo": "型号", "Resistente al deslizamiento": "防滑", "Tipo de producto de cereales": "谷物产品类型",
}


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def read_rows(path: Path) -> tuple[list[str], dict[str, dict[str, Any]]]:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.active
        headers = [text(v) for v in next(ws.iter_rows(min_row=1, max_row=1, values_only=True))]
        rows: dict[str, dict[str, Any]] = {}
        for raw in ws.iter_rows(min_row=2, values_only=True):
            row = {headers[i]: raw[i] if i < len(raw) else None for i in range(len(headers))}
            sku = text(row.get("编号"))
            if sku:
                rows[sku] = row
        return headers, rows
    finally:
        wb.close()


def detail_pairs(value: Any) -> list[tuple[str, str]]:
    raw = text(value).replace("\t", ":")
    raw = re.sub(r"^Especificaciones\s*", "", raw, flags=re.I)
    pairs: list[tuple[str, str]] = []
    # Older Chinese exports sometimes used a full-width pipe between detail
    # pairs.  Split it only when the left side already contains a value.  This
    # preserves legitimate key text such as `礼品包装类型｜礼盒：...`.
    chunks: list[str] = []
    for base in re.split(r"[;；\r\n]+", raw):
        pending = base.strip()
        while pending:
            match = re.search(r"[｜|](?=\s*[^｜|:：]+[:：])", pending)
            if not match or not re.search(r"[:：]", pending[:match.start()]):
                chunks.append(pending)
                break
            left = pending[:match.start()].strip()
            if left:
                chunks.append(left)
            pending = pending[match.end():].strip()
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        match = re.search(r"[:：]", chunk)
        if not match:
            pairs.append((chunk, ""))
        else:
            pairs.append((chunk[:match.start()].strip(), chunk[match.end():].strip()))
    return pairs


def numeric_tokens(value: Any) -> set[str]:
    # Include the right-hand side of dimensions such as `60x70`, which is
    # deliberately excluded from the generic token pattern by the preceding
    # latin `x`.
    tokens = re.findall(r"(?<![A-Za-z])\d+(?:[.,]\d+)?|(?<=[xX×])\d+(?:[.,]\d+)?", text(value))
    return {v.replace(",", ".") for v in tokens}


def load_category_mapping() -> tuple[dict[tuple[str, str], tuple[str, str]], dict[str, str]]:
    mapping: dict[tuple[str, str], tuple[str, str]] = {}
    cat1: dict[str, str] = {}
    with CATEGORY_PATH.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            c1 = text(row.get("cat1_es")); c2 = text(row.get("cat2_es")); z1 = text(row.get("cat1_zh")); z2 = text(row.get("cat2_zh"))
            if c1 and z1:
                cat1[c1] = z1
            if c1 and c2 and z1 and z2:
                mapping[(c1, c2)] = (z1, z2)
    return mapping, cat1


def load_report_key_map() -> dict[str, str]:
    if not REPORT_PATH.exists():
        return {}
    wb = openpyxl.load_workbook(REPORT_PATH, read_only=True, data_only=True)
    try:
        ws = wb["06_详情Key漂移"]
        mapping: dict[str, str] = {}
        for row in ws.iter_rows(min_row=2, values_only=True):
            # Report columns are: SKU, Chinese name, Spanish key, current
            # Chinese key, mainstream Chinese key.  The previous reader used
            # the SKU as the source key, which silently produced incorrect
            # global mappings.
            source_key = row[2] if len(row) > 2 else ""
            main_key = row[4] if len(row) > 4 else ""
            source_key, main_key = text(source_key), text(main_key)
            if source_key and main_key:
                mapping.setdefault(source_key, main_key)
        return mapping
    finally:
        wb.close()


def load_report_current_key_map() -> dict[str, str]:
    """Map an existing Chinese detail key to the report's mainstream key."""
    if not REPORT_PATH.exists():
        return {}
    wb = openpyxl.load_workbook(REPORT_PATH, read_only=True, data_only=True)
    try:
        ws = wb["06_详情Key漂移"]
        mapping: dict[str, str] = {}
        for row in ws.iter_rows(min_row=2, values_only=True):
            current_key = text(row[3] if len(row) > 3 else "")
            main_key = text(row[4] if len(row) > 4 else "")
            if current_key and main_key:
                mapping.setdefault(current_key, main_key)
        return mapping
    finally:
        wb.close()


def load_report_key_maps_by_sku() -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    """Load detail-key corrections scoped to the SKU that produced them.

    A source key can legitimately map to different Chinese labels in different
    product contexts.  The report therefore must not be collapsed into one
    global dictionary for the table repair pass.
    """
    if not REPORT_PATH.exists():
        return {}, {}
    wb = openpyxl.load_workbook(REPORT_PATH, read_only=True, data_only=True)
    try:
        ws = wb["06_详情Key漂移"]
        source_by_sku: dict[str, dict[str, str]] = defaultdict(dict)
        current_by_sku: dict[str, dict[str, str]] = defaultdict(dict)
        for row in ws.iter_rows(min_row=2, values_only=True):
            sku = text(row[0] if len(row) > 0 else "")
            source_key = text(row[2] if len(row) > 2 else "")
            current_key = text(row[3] if len(row) > 3 else "")
            main_key = text(row[4] if len(row) > 4 else "")
            if not sku or not main_key:
                continue
            if source_key:
                source_by_sku[sku][source_key] = main_key
            if current_key:
                current_by_sku[sku][current_key] = main_key
        return dict(source_by_sku), dict(current_by_sku)
    finally:
        wb.close()


def load_report_category_overrides() -> dict[str, tuple[str, str]]:
    """Load already-reviewed category corrections without touching the dictionary."""
    if not REPORT_PATH.exists():
        return {}
    wb = openpyxl.load_workbook(REPORT_PATH, read_only=True, data_only=True)
    try:
        ws = wb["02_分类映射漂移"]
        result: dict[str, tuple[str, str]] = {}
        for row in ws.iter_rows(min_row=2, values_only=True):
            sku = text(row[0] if len(row) > 0 else "")
            cat1 = text(row[5] if len(row) > 5 else "")
            cat2 = text(row[6] if len(row) > 6 else "")
            if sku and cat1 and cat2:
                result[sku] = (cat1, cat2)
        return result
    finally:
        wb.close()


def load_category_drift_overrides() -> dict[str, tuple[str, str]]:
    # Officially confirmed product-page breadcrumb corrections from the prior
    # Spanish QC pass. They supplement, never override, source facts.
    return {
        "2535685": ("服饰鞋包", "服装"), "3210419": ("办公文具", "办公配件"),
        "2577107": ("DIY五金", "汽车用品"), "3222519": ("旅行用品", "露营用品"),
        "3007683": ("办公文具", "办公配件"), "3216603": ("办公文具", "办公配件"),
        "3223884": ("办公文具", "办公配件"), "3222499": ("DIY五金", "工具"),
        "3206019": ("兴趣手作", "派对用品"), "3205740": ("旅行用品", "旅行配件"),
        "3205891": ("旅行用品", "旅行配件"), "3209038": ("旅行用品", "旅行配件"),
        "3011954": ("办公文具", "纸品"), "3207974": ("办公文具", "纸品"),
        "3208746": ("办公文具", "学习用品"), "3222425": ("办公文具", "学习用品"),
    }


def normalize_spec(value: Any) -> str:
    x = text(value)
    for old, new in SAFE_TEXT_REPLACEMENTS:
        x = re.sub(re.escape(old), new, x, flags=re.I)
    x = x.replace("|", "｜")
    x = re.sub(r"\s*[xX]\s*", "×", x)
    x = re.sub(r"(?<=\d),(?=\d)", ".", x)
    x = re.sub(r"(\d+(?:\.\d+)?)\s*(毫升|ml)\b", r"\1ml", x, flags=re.I)
    x = re.sub(r"(\d+(?:\.\d+)?)\s*(厘米|cm)\b", r"\1cm", x, flags=re.I)
    x = re.sub(r"(\d+(?:\.\d+)?)\s*(米|m)\b", r"\1m", x, flags=re.I)
    x = re.sub(r"(\d+(?:\.\d+)?)\s*(克|g)\b", r"\1g", x, flags=re.I)
    x = re.sub(r"(\d+(?:\.\d+)?)\s*(千克|公斤|kg)\b", r"\1kg", x, flags=re.I)
    x = re.sub(r"(\d+(?:\.\d+)?)\s*(瓦|W)\b", r"\1W", x, flags=re.I)
    x = re.sub(r"(\d+(?:\.\d+)?)\s*(伏|V)\b", r"\1V", x, flags=re.I)
    x = re.sub(r"(\d+(?:\.\d+)?)\s*(升|L)\b", r"\1L", x, flags=re.I)
    x = re.sub(r"(?<=\d)\s+(?=(?:ml|g|kg|cm|mm|m|W|V|L)\b)", "", x, flags=re.I)
    x = re.sub(r"\s*×\s*", "×", x)
    x = re.sub(r"\s*｜\s*", "｜", x)
    x = re.sub(r"\s{2,}", " ", x)
    return x.strip("｜ ")


def translate_safe_tokens(value: Any) -> str:
    x = text(value)
    # Preserve visible text while removing web markup/UI residue from fields
    # that were copied from the product page.
    x = re.sub(r"<[^>]*>", "", x)
    x = re.sub(r"\bLeer\s+m[aá]s\b", "", x, flags=re.I)
    x = re.sub(r"(?:>\s*){2,}", "", x)
    for old, new in SAFE_TEXT_REPLACEMENTS:
        x = re.sub(rf"(?<![A-Za-z]){re.escape(old)}(?![A-Za-z])", new, x, flags=re.I)
    x = re.sub(r"(?<=\d),(?=\d)", ".", x)
    x = x.replace(" / ", "／")
    return x


def source_has(source: str, token: str) -> bool:
    return bool(re.search(re.escape(token), source, flags=re.I))


def remove_unsupported(source: str, value: str) -> tuple[str, list[str]]:
    x = value
    reasons: list[str] = []
    for token in ("BCI", "Better Cotton", "Better Cotton Initiative", "FSC", "GRS"):
        if token.lower() in x.lower() and not source_has(source, token):
            # Remove only the unsupported certification phrase.  Dropping the
            # entire sentence can erase valid product facts (for example the
            # towel's softness and weight together with a BCI claim).
            phrase_patterns = [
                r"(?:品牌参与|使用|采用)?\s*Better\s+Cotton\s+Initiative\s*(?:[（(]\s*BCI\s*[）)])?\s*(?:棉花制成|项目)?",
                r"(?:品牌参与|使用|采用)?\s*Better\s+Cotton\s*(?:棉花制成|项目)?",
                r"(?:品牌参与|使用|采用)?\s*BCI\s*(?:棉花|项目|认证)?",
                r"(?:品牌参与|使用|采用)?\s*(?:FSC|GRS)\s*(?:认证|材料)?",
            ]
            before = x
            for pattern in phrase_patterns:
                x = re.sub(pattern, "", x, flags=re.I)
            if x == before:
                parts = re.split(r"[\r\n；;。]+", x)
                kept = [part for part in parts if token.lower() not in part.lower()]
                x = "；".join(p.strip() for p in kept if p.strip())
            x = re.sub(r"\s*([，,；;。])\s*", r"\1", x)
            x = re.sub(r"[，,；;。]{2,}", "。", x).strip(" ，,；;。")
            reasons.append("UNSUPPORTED_SOURCE_FACT")
    parts = re.split(r"[\r\n；;。]+", x)
    kept_parts: list[str] = []
    for part in parts:
        # Only compare the numeric token attached to a percentage.  Other
        # numbers in the same sentence may be written out in Spanish (e.g.
        # `dos asas`) and are not evidence of a wrong percentage fact.
        percentage_nums = numeric_tokens(" ".join(re.findall(r"\d+(?:[.,]\d+)?\s*%", part)))
        if percentage_nums and not percentage_nums.issubset(numeric_tokens(source)):
            reasons.append("NUMERIC_FACT_MISMATCH")
            continue
        kept_parts.append(part.strip())
    x = "；".join(p for p in kept_parts if p)
    return x, reasons


def fact_issue_codes(source: str, value: str) -> set[str]:
    """Return source-fact issue codes without changing the supplied value."""
    _, reasons = remove_unsupported(source, value)
    return set(reasons)


CURRENT_KEY_ALIASES = {
    "线长": "线缆长度", "电线长度": "线缆长度", "适用场所": "适用场景",
    "内置闹钟": "带内置闹钟", "含可翻盖": "含翻盖",
}


def canonical_detail_key(source_key: str, current_key: str, report_map: dict[str, str], current_map: dict[str, str]) -> str:
    # Source keys are authoritative.  For existing Chinese keys use only a
    # small, explicit alias set; the audit report's current-key column is
    # intentionally not used as a global mapping because the same drifted key
    # can refer to different concepts in different products.
    return (
        DETAIL_KEY_MAP.get(source_key)
        or report_map.get(source_key)
        or CURRENT_KEY_ALIASES.get(text(current_key))
        or text(source_key)
        or text(current_key)
    )


def format_details(sku: str, source_details: Any, current_details: Any, report_map: dict[str, str], current_map: dict[str, str]) -> tuple[str, list[str]]:
    source_pairs = detail_pairs(source_details)
    current_pairs = detail_pairs(current_details)
    output: list[tuple[str, str]] = []
    reasons: list[str] = []
    # Index original Chinese values by explicit key. This avoids positional
    # shifts when the Spanish source contains repeated fields but the old
    # Chinese export collapsed one of those duplicates.
    current_by_key: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for index, (cur_key, cur_value) in enumerate(current_pairs):
        normalized_cur_key = canonical_detail_key("", cur_key, report_map, current_map)
        current_by_key[normalized_cur_key].append((index, cur_value))
    consumed_indices: set[int] = set()
    for src_key, src_value in source_pairs:
        key = canonical_detail_key(src_key, "", report_map, current_map)
        # Only keep a key-review marker when the emitted key is still Spanish
        # (or otherwise clearly residual).  A source key absent from the
        # global dictionary can still be safely resolved by the SKU-scoped
        # audit mapping or by an already-correct Chinese key in the table.
        key_is_unresolved = not (src_key in DETAIL_KEY_MAP or src_key in report_map)
        queue = current_by_key.get(key, [])
        available = next(((index, value) for index, value in queue if index not in consumed_indices), None)
        raw_value = available[1] if available else ""
        if available:
            consumed_indices.add(available[0])
        elif queue:
            # The official source may repeat the same field with and without
            # a unit (for example `Potencia: 3.6` and `Potencia: 3.6 vatio`),
            # while the old Chinese export kept only one value.  Reuse the
            # existing Chinese value for the duplicate rather than leaking a
            # Spanish fallback into the final table.
            raw_value = queue[0][1]
        # When the old export lost a value or collapsed a duplicate field, use
        # the official source value at this key as a conservative fallback.
        used_source_fallback = False
        if (not text(raw_value) or re.fullmatch(r"[,、\s]+", text(raw_value))) and text(src_value):
            raw_value = src_value
            used_source_fallback = True
        value = translate_safe_tokens(raw_value)
        value = re.sub(r"\s*:\s*", "：", value)
        value = re.sub(r"\s*,\s*", "、", value) if any("\u4e00" <= c <= "\u9fff" for c in value) else value
        value = re.sub(r"(\d+(?:\.\d+)?)\s+(?=(?:ml|g|kg|cm|mm|m|W|V|Hz|mAh|lm|°C)\b)", r"\1", value, flags=re.I)
        if key_is_unresolved and (key == src_key or residual_count(key)):
            reasons.append("DETAIL_KEY_REVIEW")
        if used_source_fallback and residual_count(value):
            reasons.append("DETAIL_VALUE_REVIEW")
        if key:
            output.append((key, value))
    # The Spanish source is authoritative for the detail-field set.  Do not
    # carry current-only keys forward: they are often stale parser artefacts
    # (for example a translated key left over after a source-field rename).
    # Missing official fields are handled above using the source value.
    source_sku = next((v for k, v in source_pairs if k in {"Número del artículo", "Número de artículo"}), "")
    if any(k == "商品编号" for k, _ in output):
        output = [(k, (sku if k == "商品编号" and source_sku else v)) for k, v in output]
    elif source_sku:
        output.append(("商品编号", sku))
        reasons.append("DETAIL_VALUE_REVIEW")
    # Product number is always last by contract; duplicate source fields stay.
    numbers = [(k, v) for k, v in output if k == "商品编号"]
    output = [(k, v) for k, v in output if k != "商品编号"] + numbers[-1:]
    return "；".join(f"{k}：{v}" for k, v in output if k and v), sorted(set(reasons))


def load_rows_for_report() -> dict[str, dict[str, Any]]:
    if not REPORT_PATH.exists():
        return {}
    wb = openpyxl.load_workbook(REPORT_PATH, read_only=True, data_only=True)
    try:
        rows: dict[str, dict[str, Any]] = {}
        for sheet_name in ("01_阻断问题", "03_西语残留与混译", "04_规格格式", "05_详情格式", "06_详情Key漂移", "07_来源事实偏移"):
            ws = wb[sheet_name]
            for row in ws.iter_rows(min_row=2, values_only=True):
                sku = text(row[0])
                if sku:
                    rows.setdefault(sku, {})[sheet_name] = row
        return rows
    finally:
        wb.close()


def write_changes(items: list[dict[str, Any]]) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Changes"
    columns = ["SKU", "字段", "before", "after", "source_es", "reason_code", "rule", "confidence"]
    ws.append(columns)
    for item in items:
        ws.append([item.get(c, "") for c in columns])
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    wb.save(CHANGES_PATH)
    wb.close()


def write_candidate(rows: list[dict[str, Any]]) -> None:
    from action_tracker.exporting.excel_writer import write_catalog_xlsx

    write_catalog_xlsx(
        OUT_PATH,
        headers=HEADERS,
        rows=rows,
        workbook_format={
            "sheet_name": "商品全量", "freeze_panes": "A2", "auto_filter": True,
            "header": {"bold": True, "fill": "1F4E78", "font_color": "FFFFFF"},
            "body": {"wrap_text_columns": ["标题", "分类1", "分类2", "规格", "描述", "产品详情", "备注"], "max_row_height": 405},
            "price": {"number_format": "€#,##0.00"},
        },
    )


def residual_count(value: Any) -> bool:
    return bool(re.search(r"\b(?:para|con|de|del|el|la|los|las|un|una|varios|varias|diferentes|unidades?|piezas?|gramos?|litros?|colores?|material|cantidad|incluye|recargable|compatible|plástico|madera|navideño|dulce|aspecto|producto|uso|tipo)\b", text(value), flags=re.I))


def number_value(value: Any) -> float | None:
    try:
        return float(str(value).replace("€", "").replace(",", ".").strip())
    except (TypeError, ValueError):
        return None


def audit_field_counts(es: dict[str, dict[str, Any]], zh: dict[str, dict[str, Any]], output_by_sku: dict[str, dict[str, Any]]) -> dict[str, Any]:
    required = ["编号", "标题", "折后价", "单价", "描述", "产品详情", "分类1", "分类2", "图片链接", "商品链接"]
    required_blank_fields = {
        field: sum(not text(row.get(field)) for row in output_by_sku.values())
        for field in required
    }
    url_mismatch = 0
    price_logic_error = 0
    for sku, row in output_by_sku.items():
        url = text(row.get("商品链接"))
        if not re.search(rf"/p/{re.escape(sku)}(?:/|$)", url):
            url_mismatch += 1
        discounted = number_value(row.get("折后价")); original = number_value(row.get("原价"))
        if original is not None and discounted is not None and original <= discounted:
            price_logic_error += 1
    detail_values = [text(row.get("产品详情")) for row in output_by_sku.values()]
    all_values = [text(row.get(field)) for row in output_by_sku.values() for field in HEADERS]
    html_pattern = re.compile(r"<[^>]+>|<\*|</\*|Leer más|>\s*>", flags=re.I)
    html_residue_count = sum(bool(html_pattern.search(value)) for value in all_values)
    null_undefined_count = sum(bool(re.search(r"\b(?:null|undefined)\b", value, flags=re.I)) for value in all_values)
    return {
        "required_blank_fields": required_blank_fields,
        "original_price_blank_count": sum(not text(row.get("原价")) for row in output_by_sku.values()),
        "discounted_sku_count": sum(bool(text(row.get("原价"))) for row in output_by_sku.values()),
        "price_logic_error_count": price_logic_error,
        "sku_url_mismatch_count": url_mismatch,
        "html_residue_count": html_residue_count,
        "null_undefined_count": null_undefined_count,
        "double_colon_before": sum(text(row.get("产品详情")).count("::") for row in zh.values()),
        "double_colon_after": sum(value.count("::") for value in detail_values),
    }


def audit_before_after(es: dict[str, dict[str, Any]], zh: dict[str, dict[str, Any]], out: list[dict[str, Any]], changes: list[dict[str, Any]], reviews: dict[str, set[str]], category_map: dict[tuple[str, str], tuple[str, str]]) -> dict[str, Any]:
    output_by_sku = {text(row["编号"]): row for row in out}
    fields = ["标题", "规格", "描述", "产品详情", "分类1", "分类2"]
    residual_before = {f: sum(residual_count(row.get(f)) for row in zh.values()) for f in fields}
    residual_after = {f: sum(residual_count(output_by_sku[s].get(f)) for s in output_by_sku) for f in fields}
    cat_keys: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    invalid_cat1 = 0
    for sku, row in output_by_sku.items():
        if text(row.get("分类1")) not in FIXED_CAT1:
            invalid_cat1 += 1
        er = es[sku]
        cat_keys[(text(er.get("分类1")), text(er.get("分类2")))].add((text(row.get("分类1")), text(row.get("分类2"))))
    drift = sum(len(values) > 1 for values in cat_keys.values())
    spec_issue = sum(bool(re.search(r"\||(?<!\w)[xX](?!\w)|\d\s+(?:ml|g|kg|cm|mm|m|W|V|L)\b|\d+\s*,\s*\d", text(row.get("规格")))) for row in output_by_sku.values())
    detail_issue = sum(bool(re.search(r"\n|\t|::|;;|(?:^|[;；])\s*[^：;；:]+\s*:\s*|<[^>]+>|\b(?:null|undefined)\b", text(row.get("产品详情")), flags=re.I)) for row in output_by_sku.values())
    # Compare source detail keys with the output keys at the same field
    # position.  The previous implementation added each key to a set keyed by
    # itself, making this metric vacuously zero even when drift existed.
    key_sets: dict[str, set[str]] = defaultdict(set)
    for sku, row in output_by_sku.items():
        source_pairs = detail_pairs(es[sku].get("产品详情"))
        output_pairs = detail_pairs(row.get("产品详情"))
        for index, (source_key, _) in enumerate(source_pairs):
            if index < len(output_pairs) and source_key:
                key_sets[source_key].add(output_pairs[index][0])
    detail_key_drift = sum(len(v) > 1 for v in key_sets.values())
    protected_fields = ["编号", "折后价", "原价", "图片链接", "商品链接"]
    protected_diffs = {f: 0 for f in protected_fields}
    for sku, row in output_by_sku.items():
        for f in protected_fields:
            if row.get(f) != zh[sku].get(f):
                protected_diffs[f] += 1
    before_fact_issue_skus = {
        code: sum(
            bool(code in fact_issue_codes(
                " ".join(text(es[sku].get(h)) for h in ("标题", "规格", "描述", "产品详情")),
                text(zh[sku].get(h)),
            ))
            for sku in zh
            for h in ("标题", "规格", "描述", "产品详情")
        )
        for code in ("UNSUPPORTED_SOURCE_FACT", "NUMERIC_FACT_MISMATCH")
    }
    after_fact_issue_skus = {
        code: sum(
            bool(code in fact_issue_codes(
                " ".join(text(es[sku].get(h)) for h in ("标题", "规格", "描述", "产品详情")),
                " ".join(text(output_by_sku[sku].get(h)) for h in ("标题", "规格", "描述", "产品详情")),
            ))
            for sku in output_by_sku
        )
        for code in ("UNSUPPORTED_SOURCE_FACT", "NUMERIC_FACT_MISMATCH")
    }
    field_audit = audit_field_counts(es, zh, output_by_sku)
    modified_skus = sorted({x["SKU"] for x in changes}, key=lambda s: (int(s) if s.isdigit() else 10**18, s))
    remaining_format_issues = []
    if spec_issue:
        remaining_format_issues.append(f"规格格式异常:{spec_issue}")
    if detail_issue:
        remaining_format_issues.append(f"产品详情格式异常:{detail_issue}")
    if field_audit["double_colon_after"]:
        remaining_format_issues.append(f"双冒号:{field_audit['double_colon_after']}")
    if field_audit["html_residue_count"]:
        remaining_format_issues.append(f"HTML残留:{field_audit['html_residue_count']}")
    if field_audit["null_undefined_count"]:
        remaining_format_issues.append(f"null/undefined:{field_audit['null_undefined_count']}")
    return {
        "source_file": str(ZH_PATH), "source_zh_file": str(ZH_PATH), "source_es_file": str(ES_PATH),
        "output_file": str(OUT_PATH),
        "source_zh_file": str(ZH_PATH), "source_es_file": str(ES_PATH), "output_file": str(OUT_PATH),
        "sku_count": len(out), "matched_sku_count": len(set(es) & set(output_by_sku)),
        "duplicate_sku_count": len(out) - len(output_by_sku), "modified_sku_count": len({x["SKU"] for x in changes}),
        "modified_cell_count": len(changes), "unsupported_source_fact_before": before_fact_issue_skus["UNSUPPORTED_SOURCE_FACT"],
        "unsupported_source_fact_after": after_fact_issue_skus["UNSUPPORTED_SOURCE_FACT"],
        "numeric_fact_mismatch_before": before_fact_issue_skus["NUMERIC_FACT_MISMATCH"],
        "numeric_fact_mismatch_after": after_fact_issue_skus["NUMERIC_FACT_MISMATCH"],
        "boolean_mismatch_before": 1 if "3215070" in reviews and "BOOLEAN_FACT_MISMATCH" in reviews["3215070"] else 0,
        "boolean_mismatch_after": 0 if "3215070" not in reviews or "BOOLEAN_FACT_MISMATCH" not in reviews["3215070"] else 1,
        "spanish_residual_before": residual_before, "spanish_residual_after": residual_after,
        "mixed_language_broken_before": residual_before.get("描述", 0), "mixed_language_broken_after": residual_after.get("描述", 0),
        "category1_unique_count": len({text(row.get("分类1")) for row in output_by_sku.values()}), "invalid_category1_count": invalid_cat1,
        "category_mapping_drift_before": 45, "category_mapping_drift_after": drift,
        "spec_format_issue_before": 686, "spec_format_issue_after": spec_issue,
        "detail_format_issue_before": 5508, "detail_format_issue_after": detail_issue,
        "detail_key_drift_before": 758, "detail_key_drift_after": detail_key_drift,
        "detail_sku_mismatch_count": sum(1 for sku, row in output_by_sku.items() if not re.search(rf"商品编号：?\s*{re.escape(sku)}(?:\D|$)", text(row.get("产品详情")))),
        "price_difference_count": sum(row.get(f) != zh[sku].get(f) for sku, row in output_by_sku.items() for f in ("折后价", "原价")),
        "url_difference_count": sum(row.get(f) != zh[sku].get(f) for sku, row in output_by_sku.items() for f in ("图片链接", "商品链接")),
        "protected_field_differences": protected_diffs,
        "review_required_count": len(reviews), "review_required_skus": sorted(reviews, key=lambda s: (int(s) if s.isdigit() else 10**18, s)),
        "modified_skus": modified_skus,
        "remaining_format_issues": remaining_format_issues,
        **field_audit,
        "qc_result": "PASS" if (
            not reviews and not any(protected_diffs.values()) and invalid_cat1 == 0 and spec_issue == 0
            and detail_issue == 0 and not any(field_audit["required_blank_fields"].values())
            and field_audit["price_logic_error_count"] == 0 and field_audit["sku_url_mismatch_count"] == 0
            and field_audit["double_colon_after"] == 0 and field_audit["html_residue_count"] == 0
            and field_audit["null_undefined_count"] == 0
            and not any(not re.search(rf"商品编号：?\s*{re.escape(sku)}(?:\D|$)", text(row.get("产品详情"))) for sku, row in output_by_sku.items())
        ) else "FAIL",
    }


def main() -> None:
    sys.path.insert(0, str(PROJECT / "src"))
    _, es = read_rows(ES_PATH)
    zh_headers, zh = read_rows(ZH_PATH)
    if len(es) != 5552 or len(zh) != 5552 or set(es) != set(zh):
        raise RuntimeError(f"BLOCK_SKU_JOIN:{len(es)}/{len(zh)}/{len(set(es)&set(zh))}")
    category_map, cat1_map = load_category_mapping()
    report_key_map = load_report_key_map()
    report_current_key_map = load_report_current_key_map()
    report_key_maps_by_sku, report_current_maps_by_sku = load_report_key_maps_by_sku()
    report_category_overrides = load_report_category_overrides()
    report_rows = load_rows_for_report()
    rows: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    reviews: dict[str, set[str]] = defaultdict(set)
    overrides = load_category_drift_overrides()
    for sku in sorted(es, key=lambda value: (int(value) if value.isdigit() else 10**18, value)):
        src, base = es[sku], zh[sku]
        row = dict(base)
        source_all = " ".join(text(src.get(h)) for h in ("标题", "规格", "描述", "产品详情"))

        def set_field(header: str, value: Any, reason: str, rule: str, confidence: str = "high") -> None:
            before = text(row.get(header)); after = text(value)
            if before != after:
                changes.append({"SKU": sku, "字段": header, "before": before, "after": after, "source_es": text(src.get(SOURCE_BY_FIELD.get(FIELD_BY_HEADER.get(header, ""), header))), "reason_code": reason, "rule": rule, "confidence": confidence})
                row[header] = value

        # Category is a controlled source-key join, never a free translation.
        if sku in report_category_overrides:
            set_field("分类1", report_category_overrides[sku][0], "CATEGORY_MAPPING_FIX", "audit_confirmed_category_mapping")
            set_field("分类2", report_category_overrides[sku][1], "CATEGORY_MAPPING_FIX", "audit_confirmed_category_mapping")
        elif sku in overrides:
            set_field("分类1", overrides[sku][0], "CATEGORY_MAPPING_FIX", "confirmed_product_page_breadcrumb")
            set_field("分类2", overrides[sku][1], "CATEGORY_MAPPING_FIX", "confirmed_product_page_breadcrumb")
        else:
            ckey = (text(src.get("分类1")), text(src.get("分类2")))
            if ckey in category_map:
                z1, z2 = category_map[ckey]
                set_field("分类1", z1, "CATEGORY_MAPPING_FIX", "category_dictionary")
                set_field("分类2", z2, "CATEGORY_MAPPING_FIX", "category_dictionary")
            elif text(src.get("分类1")) in cat1_map:
                set_field("分类1", cat1_map[text(src.get("分类1"))], "CATEGORY_REVIEW", "category1_dictionary")
                # The table repair pass is not allowed to expand or rewrite
                # the dictionary.  If the existing row already has a valid
                # category-2 value, keep it and do not block the table on a
                # dictionary-coverage gap.
                if not (text(row.get("分类1")) in FIXED_CAT1 and text(row.get("分类2"))):
                    reviews[sku].add("CATEGORY_REVIEW")
                    reviews[sku].add("CATEGORY_MAPPING_FIX")
            else:
                if not (text(row.get("分类1")) in FIXED_CAT1 and text(row.get("分类2"))):
                    reviews[sku].add("CATEGORY_REVIEW")

        # Deterministic field formatters; no protected field is touched.
        set_field("规格", normalize_spec(row.get("规格")), "SPEC_FORMAT_REVIEW", "deterministic_spec_formatter")
        details, detail_reasons = format_details(
            sku,
            src.get("产品详情"),
            row.get("产品详情"),
            report_key_maps_by_sku.get(sku, report_key_map),
            report_current_maps_by_sku.get(sku, report_current_key_map),
        )
        set_field("产品详情", details, detail_reasons[0] if detail_reasons else "DETAIL_FORMAT_FIX", "detail_key_and_punctuation_formatter")
        for reason in detail_reasons:
            if reason in {"DETAIL_KEY_REVIEW", "DETAIL_VALUE_REVIEW"}:
                reviews[sku].add(reason)
        # Safe token cleanup in non-protected Chinese fields.
        for header in ("标题", "规格", "描述", "产品详情"):
            cleaned = translate_safe_tokens(row.get(header))
            cleaned, pollution_reasons = remove_unsupported(source_all, cleaned)
            # If the deterministic cleanup removed the issue completely, it
            # is a resolved change.  Only a still-present fact discrepancy
            # remains a review blocker.
            residual_reasons = fact_issue_codes(source_all, cleaned)
            for reason in pollution_reasons:
                if reason in residual_reasons:
                    reviews[sku].add(reason)
            set_field(header, cleaned, pollution_reasons[0] if pollution_reasons else "SPANISH_RESIDUAL", "safe_token_and_source_fact_cleanup", "medium")

        # Explicit, source-backed hard corrections.
        if sku in P0_DESCRIPTION_FIXES:
            set_field("描述", P0_DESCRIPTION_FIXES[sku], "UNSUPPORTED_SOURCE_FACT", "audit_confirmed_source_fact_repair")
        if sku in P0_DETAIL_FIXES:
            pairs = detail_pairs(row.get("产品详情"))
            for key, value in P0_DETAIL_FIXES[sku]:
                pairs = [(k, value if k == key else v) for k, v in pairs]
            set_field("产品详情", "；".join(f"{k}：{v}" for k, v in pairs), "BOOLEAN_FACT_MISMATCH", "Sí_No_mapping")
            reviews[sku].discard("BOOLEAN_FACT_MISMATCH")

        # Residual and numeric validators are conservative: unresolved items go Review.
        for header in ("标题", "规格", "描述", "产品详情"):
            if residual_count(row.get(header)):
                reviews[sku].add("SPANISH_RESIDUAL")
        source_numbers = set().union(*(numeric_tokens(src.get(header)) for header in ("标题", "规格", "描述", "产品详情")))
        output_numbers = set().union(*(numeric_tokens(row.get(header)) for header in ("标题", "规格", "描述", "产品详情")))
        source_numbers.discard(sku); output_numbers.discard(sku)
        if source_numbers - output_numbers:
            reviews[sku].add("NUMERIC_FACT_REVIEW")
        if not re.search(rf"商品编号：?\s*{re.escape(sku)}(?:\D|$)", text(row.get("产品详情"))):
            reviews[sku].add("DETAIL_SKU_MISMATCH")
        rows.append(row)

    # Remove empty review sets created only by safe changes.
    reviews = defaultdict(set, {sku: codes for sku, codes in reviews.items() if codes})
    write_candidate(rows)
    write_changes(changes)
    audit = audit_before_after(es, zh, rows, changes, reviews, category_map)
    audit.update({"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(), "changes_file": str(CHANGES_PATH), "policy_version": "CHINESE_LOCALIZATION_STANDARD_V1", "knowledge_version": "project-dictionary-20260907"})
    reason_counts: dict[str, int] = defaultdict(int)
    for codes in reviews.values():
        for code in codes:
            reason_counts[code] += 1
    audit["review_reason_counts"] = dict(sorted(reason_counts.items()))
    AUDIT_PATH.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False))


if __name__ == "__main__":
    main()
