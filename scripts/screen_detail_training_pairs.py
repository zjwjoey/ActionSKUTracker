"""Select balanced, review-only detail translation pairs from SQLite PRIMARY."""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from scripts.build_local_qwen_dataset import (
    DETAIL_DICTIONARY_HEADERS,
    _known_tokens,
    _numeric_ok,
    _target_ok,
)


CATEGORIES_ZH = {
    "Bricolaje": "DIY五金",
    "Oficina y papelería": "办公文具",
    "Mascotas": "宠物用品",
    "Cocina": "厨房餐具",
    "Moda": "服饰鞋包",
    "Cuidado personal": "个人美容",
    "Vivienda": "家居布置",
    "Hogar": "家务清洁",
    "Viajes": "旅行用品",
    "Comer y beber": "食品饮料",
    "Multimedia": "数码影音",
    "Juguetes": "玩具",
    "Hobby": "兴趣手作",
    "Jardín": "园艺户外",
    "Artículos deportivos": "运动用品",
}

# Only unambiguous, repeatedly observed detail-key mappings are accepted at
# this stage.  Context-dependent keys such as ``Cubierta`` and mechanically
# mistranslated keys such as ``Talla cabeza`` are intentionally absent.
SAFE_DETAIL_KEYS = {
    "Número del artículo": "商品编号",
    "Color": "颜色",
    "Material": "材质",
    "Cantidad": "数量",
    "Contenido": "含量",
    "Instrucciones de lavado": "洗涤说明",
    "Instrucciones de secado": "干燥说明",
    "Instrucciones de planchado": "熨烫说明",
    "Forma": "形状",
    "Género": "适用性别",
    "Consejos sobre conservación": "储存建议",
    "Edad adecuada": "适用年龄",
    "Energía": "能量",
    "Carbohidratos": "碳水化合物",
    "De los cuales azúcares": "其中糖分",
    "Proteínas": "蛋白质",
    "Grasas": "脂肪",
    "Sal": "盐分",
    "De los cuales saturados": "其中饱和脂肪",
    "Apto para el lavavajillas": "可用洗碗机清洗",
    "Uso previsto": "用途",
    "Sabor": "口味",
    "Medidas (incl. envase) (largo x ancho x alto)": "尺寸(含包装,长×宽×高)",
    "Tipo de cierre": "闭合类型",
    "Instalación": "安装方式",
    "Apto para el microondas": "可用微波炉加热",
    "Destinado a": "适用场所",
    "Tipo de alimentación": "供电方式",
    "Método de fijación": "固定方式",
    "Sin perfume": "不含香精",
    "Con tapa": "带盖子",
    "Talla de las prendas de vestir": "服装尺码",
    "Apto para tipo de animal (de compañía)": "适用宠物类型",
    "Resistente al horno": "烤箱安全",
    "Sin azúcar": "无糖",
    "Pilas incluidas": "含电池",
    "Apto para tipo de parte del cuerpo": "适用身体部位",
    "Talla de los calcetines": "袜子尺码",
    "Fibras": "膳食纤维",
    "Longitud calcetines": "袜子长度",
    "Olor": "气味",
    "Función de limpieza": "清洁功能",
    "Número de pilas necesarias": "所需电池数量",
    "Tipo de textil": "面料类型",
    "Incluye fuente de luz": "含光源",
    "Perfumado": "带香味",
    "Código de batería": "电池型号",
    "Contiene alcohol": "含酒精",
    "Rellenable": "可重复填充",
    "Tipo de fuente de luz": "光源类型",
    "Tipo de vela": "蜡烛类型",
    "Listo para su uso": "开箱即用",
    "Tipo de pienso para animales (domésticos)": "宠物饲料类型",
    "Tipo de pienso (doméstico)": "饲料类型",
    "Información sobre alérgenos": "过敏原信息",
    "Halal": "清真",
    "Número de páginas": "页数",
    "Formato del papel": "纸张规格",
    "Incluye mando a distancia": "含遥控器",
    "Tipo de encuadernación": "装订方式",
    "Número de horas de combustión": "燃烧小时数",
    "Vegano": "纯素",
    "Longitud de la manga": "袖长",
    "Tipo de agarre": "握持方式",
    "Incluye materiales de montaje": "含安装材料",
    "Antideslizante": "防滑",
    "Apto para tipo de transporte": "适用出行方式",
    "Número de compartimentos": "隔层数量",
    "Sin alcohol": "不含酒精",
    "Tema": "主题",
    "Capacidad máxima de carga": "最大承重",
    "Sin gluten": "无麸质",
    "Con mango": "带手柄",
    "Material de la superficie del objetivo": "目标表面材质",
    "Recargable": "可充电",
    "Número de hojas": "张数",
    "Apto para tipo de piel": "适用肤质",
    "Modelo": "型号",
    "Incluye accesorios": "含配件",
    "Tipo de alimentación para mascotas": "宠物食品类型",
    "Tipo de caramelo": "糖果类型",
    "Tipo de bolígrafo": "圆珠笔类型",
    "Extensible": "可伸缩",
    "Inalámbrica": "无线",
    "Incluye sonido": "带声音",
    "Regulable": "可调光",
    "Lumen": "流明",
    "Sin lactosa": "无乳糖",
    "Con interruptor de encendido y apagado": "带电源开关",
}


def _detail_keys_are_safe(source: str, target: str) -> bool:
    source_pairs = [part.split(":", 1)[0].strip() for part in re.split(r"[;\n]", source) if ":" in part]
    target_pairs = [re.split(r"[:：]", part, 1)[0].strip() for part in re.split(r"[；;\n]", target) if ":" in part or "：" in part]
    return bool(source_pairs) and len(source_pairs) == len(target_pairs) and all(
        SAFE_DETAIL_KEYS.get(source_key) == target_key
        for source_key, target_key in zip(source_pairs, target_pairs)
    )


def _quality_score(row: dict[str, str]) -> float:
    desc_es, desc_zh = row["description_es_raw"], row["description_zh_standard"]
    details_es, details_zh = row["details_es_raw"], row["details_zh_standard"]
    desc_ratio = len(desc_zh) / len(desc_es)
    details_ratio = len(details_zh) / len(details_es)
    # Prefer representative medium-length retail copy and typical Chinese /
    # Spanish compression ratios.  This score is only for deterministic
    # ranking after all hard safety gates have passed.
    return (
        100
        - abs(len(desc_es) - 190) / 15
        - abs(len(details_es) - 190) / 18
        - abs(desc_ratio - 0.21) * 80
        - abs(details_ratio - 0.38) * 80
        - abs(details_es.count(";") - 5) * 0.8
    )


def screen(database: Path, dictionary_dir: Path, per_category: int = 20) -> tuple[list[dict[str, str]], list[dict[str, object]], dict[str, object]]:
    tokens = _known_tokens(dictionary_dir)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        select p.official_sku, e.name name_es, e.cat1 cat1_es, e.cat2 cat2_es,
               e.spec spec_es, e.description desc_es, e.details details_es,
               e.source_hash es_hash, z.description desc_zh, z.details details_zh,
               z.source_hash zh_hash, z.review_status, z.freshness_status,
               z.last_commit_id
          from products p
          join product_localizations e
            on e.official_sku=p.official_sku and e.language='es'
          join product_localizations z
            on z.official_sku=p.official_sku and z.language='zh'
         where p.status='CURRENT'
        """
    ).fetchall()
    connection.close()
    eligible: dict[str, list[dict[str, str]]] = defaultdict(list)
    rejected: dict[str, int] = defaultdict(int)
    seen_pairs: set[tuple[str, str, str, str]] = set()
    for raw in rows:
        item = {key: "" if raw[key] is None else str(raw[key]).strip() for key in raw.keys()}
        category = item["cat1_es"]
        desc_es, desc_zh = item["desc_es"], item["desc_zh"]
        details_es, details_zh = item["details_es"], item["details_zh"]
        checks = {
            "known_category": category in CATEGORIES_ZH,
            "status": item["review_status"] == "PENDING",
            "fresh": item["freshness_status"] == "CURRENT" and item["es_hash"] == item["zh_hash"],
            "complete": all((desc_es, desc_zh, details_es, details_zh)),
        }
        if checks["complete"]:
            checks.update({
                "different_language": desc_es.casefold() != desc_zh.casefold() and details_es.casefold() != details_zh.casefold(),
                "cjk": bool(re.search(r"[\u3400-\u9fff]", desc_zh)) and bool(re.search(r"[\u3400-\u9fff]", details_zh)),
                "not_damaged": "�" not in desc_zh + details_zh and not re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", desc_zh + details_zh),
                "numbers": _numeric_ok(desc_es, desc_zh) and _numeric_ok(details_es, details_zh),
                "language": _target_ok(desc_zh, tokens) and _target_ok(details_zh, tokens),
                "detail_keys": _detail_keys_are_safe(details_es, details_zh),
                "sku": item["official_sku"] in details_zh,
                "length": 80 <= len(desc_es) <= 500 and 60 <= len(details_es) <= 450,
                "ratio": 0.10 <= len(desc_zh) / len(desc_es) <= 0.50 and 0.25 <= len(details_zh) / len(details_es) <= 0.55,
            })
        failed = [name for name, passed in checks.items() if not passed]
        if failed:
            for reason in failed:
                rejected[reason] += 1
            continue
        pair_key = (desc_es, desc_zh, details_es, details_zh)
        if pair_key in seen_pairs:
            rejected["duplicate_pair"] += 1
            continue
        seen_pairs.add(pair_key)
        eligible[category].append({
            "sku": item["official_sku"],
            "source_hash": item["zh_hash"],
            "name_es_raw": item["name_es"],
            "cat1_es": category,
            "cat2_es": item["cat2_es"],
            "spec_es_raw": item["spec_es"],
            "description_es_raw": desc_es,
            "description_zh_standard": desc_zh,
            "details_es_raw": details_es,
            "details_zh_standard": details_zh,
            "review_status": "TRAINING_APPROVED",
            "locked": "0",
            "source_run_id": "",
            "source_commit_id": item["last_commit_id"],
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "notes": "User-authorized for offline training only; not a production approval",
        })
    selected: list[dict[str, str]] = []
    category_counts: dict[str, int] = {}
    ranked_by_category: dict[str, list[dict[str, str]]] = {}
    for category in CATEGORIES_ZH:
        ranked = sorted(eligible.get(category, []), key=lambda row: (-_quality_score(row), row["sku"]))
        ranked_by_category[category] = ranked
        chosen = ranked[:per_category]
        category_counts[category] = len(chosen)
        selected.extend(chosen)
    # Preserve representation for scarce categories, then fill any shortfall
    # from the strongest remaining records across all categories.
    target_total = per_category * len(CATEGORIES_ZH)
    selected_skus = {row["sku"] for row in selected}
    remaining = sorted(
        (row for values in ranked_by_category.values() for row in values if row["sku"] not in selected_skus),
        key=lambda row: (-_quality_score(row), row["sku"]),
    )
    for row in remaining[:max(0, target_total - len(selected))]:
        selected.append(row)
        category_counts[row["cat1_es"]] += 1
    review_rows: list[dict[str, object]] = []
    for row in sorted(selected, key=lambda item: (list(CATEGORIES_ZH).index(item["cat1_es"]), -_quality_score(item), item["sku"])):
        category = row["cat1_es"]
        review_rows.append({
            "训练状态": "训练已授权",
            "SKU": row["sku"],
            "一级类目": CATEGORIES_ZH[category],
            "西语品名": row["name_es_raw"],
            "西语规格": row["spec_es_raw"],
            "西语描述": row["description_es_raw"],
            "中文描述": row["description_zh_standard"],
            "西语详情": row["details_es_raw"],
            "中文详情": row["details_zh_standard"],
            "数字保留": "通过",
            "商品编号": "通过",
            "西语残留": "未检出",
            "source_hash": row["source_hash"],
        })
    summary = {
        "current_rows_scanned": len(rows),
        "strict_eligible": sum(len(value) for value in eligible.values()),
        "selected": len(selected),
        "per_category_target": per_category,
        "category_counts": category_counts,
        "rejected": dict(sorted(rejected.items())),
        "production_apply": False,
    }
    return selected, review_rows, summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--dictionary-dir", type=Path, required=True)
    parser.add_argument("--training-csv", type=Path, required=True)
    parser.add_argument("--review-json", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--per-category", type=int, default=20)
    args = parser.parse_args()
    selected, review_rows, summary = screen(args.database, args.dictionary_dir, args.per_category)
    args.training_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.training_csv.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=DETAIL_DICTIONARY_HEADERS)
        writer.writeheader(); writer.writerows(selected)
    args.review_json.write_text(json.dumps(review_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    args.summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
