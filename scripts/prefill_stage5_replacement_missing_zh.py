"""Fill the 15 missing Chinese description/detail fields for Stage-5 review.

This is a review artifact only.  It never writes the dictionary, Master, or
training split.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


TRANSLATIONS = {
    "2563856": {
        "description_zh": "更多巧克力；著名Haribo棉花糖，外层覆有巧克力，带来浓郁风味。",
        "details_zh": "碳水化合物：73g；含量：300g；其中糖：62g；其中饱和脂肪：12g；能量：420kcal；脂肪：13g；蛋白质：3.5g；盐：0.08g；糖果类型：棉花糖；商品编号：2563856",
    },
    "3215124": {
        "description_zh": "配有备用拖把头；伸缩杆最长可延伸至120cm；适用于瓷砖、层压地板和镶木地板。",
        "details_zh": "颜色：蓝色、灰色；材质：棉；数量：2件；洗涤说明：最高40°C机洗；烘干说明：不可滚筒烘干；商品编号：3215124",
    },
    "3218000": {
        "description_zh": "非常温暖柔软；可选Spidey、米老鼠、Stitch等图案；适合节日穿着。",
        "details_zh": "童装尺码：134-140；颜色：多色；性别：中性；洗涤说明：最高30°C机洗；熨烫说明：不可熨烫；烘干说明：不可滚筒烘干；袖长：长袖；外层材质：棉；领型：圆领；毛衣/卫衣类型：卫衣；保暖：否；商品编号：3218000",
    },
    "3218278": {
        "description_zh": "8种不同功能的圣诞灯饰；暖白光；带定时器。这款欢快的圣诞灯饰易于放置在地面上，使用3节AAA电池（需另购），有圣诞树或圣诞棒棒糖等多种造型。",
        "details_zh": "颜色：绿色、红色；柔和色调：暖白色；数量：3件；电池型号：AA；适用场景：户外；含光源：是；安装方式：立式；所需电池数量：3；含电池：否；主题：圣诞节；供电类型：电池；季节装饰类型：季节装饰；光源类型：LED灯；季节：冬季；光源数量：42；商品编号：3218278",
    },
    "3219600": {
        "description_zh": "带LED灯；为圣诞村增添可爱装饰。圣诞村中少不了圣诞老人和姜饼屋。这套6件套装可为圣诞村增添装饰，多色LED灯光让夜晚更有氛围。",
        "details_zh": "数量：6件；电池型号：AAA；含光源：是；安装方式：立式；所需电池数量：2；含电池：否；主题：圣诞节；供电类型：电池；季节装饰类型：季节装饰；季节：冬季；商品编号：3219600",
    },
    "3219963": {
        "description_zh": "唇形趣味设计；包含眼影、指甲油和唇彩。用这套美容套装轻松焕新妆容，所有物品都能收纳在醒目的唇形盒中，适合儿童派对和庆祝活动。",
        "details_zh": "颜色：多色；数量：6件；商品编号：3219963",
    },
    "3223174": {
        "description_zh": "配有10个实用隔层；包含可折叠镜子；采用FSC认证竹材。这个竹制收纳盒可让书桌或梳妆台保持整洁，10个隔层方便收纳首饰、化妆品和其他小物品，内置镜子便于快速整理仪容。",
        "details_zh": "尺寸（含包装）（长×宽×高）：19.9×12.9×8.8cm；颜色：棕色；材质：竹、木；安装方式：立式；化妆工具/配件类型：化妆盒；商品编号：3223174",
    },
    "3224259": {
        "description_zh": "可挂在牵引绳上；包含2卷、每卷20个垃圾袋。",
        "details_zh": "颜色：黑色、蓝色、绿色、红色；材质：塑料；数量：41件；商品编号：3224259",
    },
    "3224285": {
        "description_zh": "每团100米；适合使用2号钩针进行精细钩织。柔软蓬松的绒线适合制作小型毛绒玩具、钥匙扣、毯子和靠垫上的装饰边或细节，多种颜色可选。",
        "details_zh": "尺寸（含包装）（长×宽×高）：20×10×10cm；颜色：蓝色、绿色、橙色、粉色、白色、黑色；材质：聚酯纤维；含钩针：否；洗涤说明：最高30°C机洗；商品编号：3224285",
    },
    "3224717": {
        "description_zh": "可用多种配料装饰披萨；包含披萨刀、酱料和其他配料等配件。孩子可以选择配料装饰披萨、切成小块并与家人朋友分享，适合厨房角色扮演游戏。",
        "details_zh": "尺寸（含包装）（长×宽×高）：25×5×30cm；材质：塑料；适用年龄：18个月以上；数量：29件；含声音：否；厨房玩具类型：食物；商品编号：3224717",
    },
    "3224726": {
        "description_zh": "便盆会发出声音；配有便盆、奶瓶、奶嘴等婴儿护理用品；使用2节AAA电池（已包含）。孩子可以给娃娃喂奶、让她坐便盆，听到声音让游戏更逼真，适合喜欢照顾娃娃的儿童。",
        "details_zh": "尺寸（含包装）（长×宽×高）：27×29×11cm；适用年龄：18个月以上；电池型号：AAA；含配件：是；含声音：是；所需电池数量：2；含电池：是；可充电：否；供电类型：电池；商品编号：3224726",
    },
    "3224781": {
        "description_zh": "每团57米；15种颜色，适合创意制作；易于操作、使用舒适。15团色彩鲜艳的绒线可用于小型手工项目和装饰，触感舒适，适合初学者。",
        "details_zh": "颜色：多色；材质：聚酯纤维；数量：15件；含钩针：否；洗涤说明：最高30°C机洗；商品编号：3224781",
    },
    "3224788": {
        "description_zh": "完整木制厨房，包含炉灶、烤箱、水槽和水龙头；配有锅、平底锅和锅铲；采用柔和的粉彩色木材。孩子可以通过炉灶、烤箱和水槽进行厨房角色扮演，柜子还能收纳配件。",
        "details_zh": "颜色：绿色；材质：FSC木材；适用年龄：3岁以上；数量：8件；含光源：否；含声音：否；厨房玩具类型：厨房套装；商品编号：3224788",
    },
    "3225101": {
        "description_zh": "包含2只小狗和多种配件；配有专用小狗包。套装包含一个娃娃、2只小狗及多种配件，可模拟散步、照护和日常冒险，奶瓶可以放入小狗嘴里，小狗也能放进包中。",
        "details_zh": "颜色：图案、多色；适用年龄：3岁以上；含配件：是；商品编号：3225101",
    },
    "3225392": {
        "description_zh": "包含装饰杯和装饰签；非常适合儿童派对；多种主题可选。用这套纸杯蛋糕装饰套装装饰甜点更有趣，适合儿童派对和庆祝活动。",
        "details_zh": "数量：32件；商品编号：3225392",
    },
}


def main(source: Path, output: Path) -> dict[str, int]:
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    filled = 0
    for row in rows:
        values = TRANSLATIONS.get(row["sku"], {})
        for field in ("description_zh", "details_zh"):
            if not row.get(field) and values.get(field):
                row[field] = values[field]; filled += 1
        row["missing_zh_fields"] = "|".join(field for field in ("description_zh", "details_zh") if not row.get(field))
        row["candidate_status"] = "GOLD_PREFILL_REVIEW_REQUIRED"
    output.parent.mkdir(parents=True, exist_ok=True)
    columns = list(rows[0]) if rows else ["sku"]
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns); writer.writeheader(); writer.writerows(rows)
    manifest = {"source_count": len(rows), "filled_field_count": filled, "remaining_missing_field_count": sum(bool(row["missing_zh_fields"]) for row in rows), "owner_decisions": 0, "training_runs": 0, "production_writes": 0, "status": "OWNER_REVIEW_REQUIRED", "csv": str(output)}
    output.with_name("stage5_replacement_gold_prefill_ai_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--source", default="runtime/training/qwen3_8b/20260915/stage5_replacement_source_versions_119/stage5_replacement_gold_prefill.csv"); parser.add_argument("--output", default="runtime/training/qwen3_8b/20260915/stage5_replacement_source_versions_119/stage5_replacement_gold_prefill_ai.csv"); args = parser.parse_args(); print(json.dumps(main(Path(args.source), Path(args.output)), ensure_ascii=False, indent=2))
