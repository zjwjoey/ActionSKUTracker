"""Create a read-only AI review package for the 18 Stage 5 canary fields.

The resulting file is a review recommendation only.  It never changes the
canary candidates, Gold, Dictionary, Master, SQLite, or production settings.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


RECOMMENDATIONS: dict[tuple[str, str], dict[str, Any]] = {
    ("2550730", "description"): {
        "disposition": "ACCEPT_AS_IS",
        "recommended_value": "适用于室内外；防泼水；带拉链和固定环；用这款印花、色彩鲜艳且防泼水的靠垫套装饰花园或阳台。",
        "rationale": "逐句对应室内外、防泼水、拉链、固定环、印花和彩色靠垫套；无事实新增。",
    },
    ("2550730", "details"): {
        "disposition": "ACCEPT_AS_IS",
        "recommended_value": "颜色：图案；材质：涤纶；形状：方形；洗涤说明：可机洗、最高30°C；防泼水：是；商品编号：2550730",
        "rationale": "字段、数值、单位和 SKU 均与西语事实一致。",
    },
    ("2570444", "description"): {
        "disposition": "RETRY_MODEL",
        "recommended_value": "直径300厘米的悬臂遮阳伞；旋转手柄和推升式系统，便于打开和倾斜；现代铝制结构；灰色涤纶面料，具有UV30+防紫外线保护。遮阳伞是充分利用花园或阳台空间、为您和客人遮挡阳光或少量雨水的实用配件，也能为户外空间增添个性。这款悬臂遮阳伞直径300厘米，可形成宽阔的遮阳区域；可轻松放置在桌子或沙发旁，安装简单：转动手柄打开遮阳伞，再使用推升式系统调节倾斜角度，从而准确决定遮阳位置。深灰色/炭灰色涤纶伞布提供UV30+防紫外线保护；配有安装套件，坚固的铝制底座有助于均匀分配重量，但安装后需要用石块配重（石块不含）。重要提示：大风天气不适合使用。",
        "rationale": "原模型未返回 JSON；建议重跑模型。该建议译文保留悬臂结构、300cm、UV30+、铝制、配重和大风限制等事实。",
    },
    ("2570444", "details"): {
        "disposition": "MINOR_EDIT",
        "recommended_value": "颜色：灰色；材质：钢、涤纶；旋转机构：是；可伸缩：是；可倾斜：是；含伞座：是；含保护套：否；安装方式：落地式；可折叠：是；防紫外线：是；商品编号：2570444",
        "rationale": "当前字段基本正确；将 base 明确为伞座、de pie 明确为落地式，避免把底座误写成伞架。",
    },
    ("3004609", "description"): {
        "disposition": "ACCEPT_WITH_GUARD_NOTE",
        "recommended_value": "产品由100%再生塑料制成；轻便，易于移动；这款大号基础花盆可容纳各种花卉和植物，由再生塑料瓶制成；使用再生塑料制作花盆，每年可节省多达200万公斤塑料。",
        "rationale": "200万公斤准确对应西语 dos millones de kilos；NUMERIC_HALLUCINATED 是守卫误报，需记录为显式例外，不应改写数值。",
    },
    ("3004609", "details"): {
        "disposition": "ACCEPT_AS_IS",
        "recommended_value": "颜色：黑色；材质：聚丙烯（PP）；形状：圆形；商品编号：3004609",
        "rationale": "字段、材质、形状及 SKU 一致。",
    },
    ("3009162", "description"): {
        "disposition": "ACCEPT_AS_IS",
        "recommended_value": "轻便，易于移动；适合户外使用；这款大号基础花盆可容纳各种花卉和植物。",
        "rationale": "与西语三句事实逐句对应。",
    },
    ("3009162", "details"): {
        "disposition": "ACCEPT_AS_IS",
        "recommended_value": "颜色：黑色；材质：塑料；商品编号：3009162",
        "rationale": "字段和值完整且 SKU 对应。",
    },
    ("3221578", "description"): {
        "disposition": "MINOR_EDIT",
        "recommended_value": "含仿杏仁膏外层和水果夹心；采用雨林联盟认证的可可制成，对人和环境更友好",
        "rationale": "mazapán 是杏仁膏/杏仁糖，不应泛化为坚果；当前“仿坚果涂层”对象和表达均不够准确。",
    },
    ("3221578", "details"): {
        "disposition": "ACCEPT_AS_IS",
        "recommended_value": "颜色：蓝色、粉色；数量：4 件；碳水化合物：75.8g；储存建议：置于阴凉干燥处保存；含量：100g；其中糖分：73.7g；其中饱和脂肪：4g；能量：417 千卡；纤维含量：1.3g；脂肪：10.5g；蛋白质：3.5g；盐分：0.09g；商品编号：3221578",
        "rationale": "营养数值、单位、颜色、数量和 SKU 均保留。",
    },
    ("3221779", "description"): {
        "disposition": "ACCEPT_AS_IS",
        "recommended_value": "傍晚自动亮起；暖白光；彩色套装；这款太阳能LED灯串配有10个彩色布灯，暖白光由太阳能供电，适合花园、阳台或派对装饰，可自动充电并在傍晚亮起。",
        "rationale": "10 个布灯、太阳能、暖白光、户外/派对用途均有原文依据。",
    },
    ("3221779", "details"): {
        "disposition": "MINOR_EDIT",
        "recommended_value": "暖白光；数量：10 件；带传感器：是；可更换光源：否；含光源：是；含遥控器：否；安装方式：挂式；固定方式：挂式；可调节：否；供电方式：太阳能；光源类型：LED灯；外部灯类型：灯笼；光源数量：10；商品编号：3221779",
        "rationale": "Farolillo 是灯笼，不是小夜灯；其余字段与西语一致。",
    },
    ("3222393", "description"): {
        "disposition": "ACCEPT_AS_IS",
        "recommended_value": "遮光并阻挡外部光线；带8个环；优雅面料；用这款遮光帘为家中创造更多宁静。适合卧室、儿童房或客厅，当你想遮光时使用。轻松挂起窗帘，立即享受更暗、更私密的空间。是良好夜间休息、午睡或愉快电影之夜的完美选择。这种中性颜色易于与任何装饰风格搭配，为你的空间增添宁静氛围。",
        "rationale": "遮光、8 个环、材质与使用场景均对应原文。",
    },
    ("3222393", "details"): {
        "disposition": "ACCEPT_AS_IS",
        "recommended_value": "颜色：米色；材质：涤纶、不锈钢；遮光帘：是；洗涤说明：可机洗、最高30°C；熨烫说明：不可熨烫；干燥说明：不可滚筒烘干；固定方式：挂式；窗帘类型：环形窗帘；商品编号：3222393",
        "rationale": "数值、材质、护理说明、安装方式及 SKU 均完整。",
    },
    ("3222921", "description"): {
        "disposition": "MINOR_EDIT",
        "recommended_value": "适用于室内外；带实用挂孔；用这款墙面装饰为室内或室外增添氛围，因为鲜艳的颜色立即吸引注意。适合为单调的墙面带来新气息，因为挂孔使其易于悬挂。得益于广泛的主题选择，总能找到适合你风格的东西，让你最喜欢的地方更加温馨。",
        "rationale": "orificios para colgar 是挂孔；“挂钩孔”和“无聊的墙面”略有增译/直译，建议收敛为挂孔、单调墙面。",
    },
    ("3222921", "details"): {
        "disposition": "ACCEPT_AS_IS",
        "recommended_value": "颜色：彩色；材质：铁；适用场景：室内外两用；形状：长方形；固定方式：挂式；商品编号：3222921",
        "rationale": "字段、材质、形状、使用范围、固定方式和 SKU 一致。",
    },
    ("3223437", "description"): {
        "disposition": "ACCEPT_AS_IS",
        "recommended_value": "磁性封页可将笔记整齐收纳；优雅设计；这款优雅笔记本将激发你写作的欲望。其柔和优雅的设计让记笔记更加有趣。实用的磁性封页即使随身携带在包或背包中也能保持井然有序。非常适合保存想法、清单和计划。适合工作、学校或在家使用。这样你随时都能找到一本漂亮的笔记本。",
        "rationale": "磁性闭合、设计、使用场景和收纳语义均保留。",
    },
    ("3223437", "details"): {
        "disposition": "ACCEPT_AS_IS",
        "recommended_value": "颜色：金色、图案；封面：硬壳；纸张规格：A5；页数：96；页面填充：横线；闭合类型：磁性闭合；装订类型：平装；商品编号：3223437",
        "rationale": "A5、96 页、硬壳、横线、磁性闭合、平装和 SKU 均完整。",
    },
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    raw = args.input.read_text(encoding="utf-8-sig")
    try:
        parsed_input = json.loads(raw)
    except json.JSONDecodeError:
        parsed_input = None
    rows = parsed_input if isinstance(parsed_input, list) else [json.loads(line) for line in raw.splitlines() if line.strip()]
    if len(rows) != len(RECOMMENDATIONS):
        rows = [row for row in rows if row.get("review_status") == "PENDING"]
    if len(rows) != len(RECOMMENDATIONS):
        raise ValueError(f"Expected {len(RECOMMENDATIONS)} canary rows, found {len(rows)}")
    review: list[dict[str, Any]] = []
    for row in rows:
        key = (str(row.get("sku", "")), str(row.get("source_field", "")))
        recommendation = RECOMMENDATIONS.get(key)
        if recommendation is None:
            raise ValueError(f"Missing review recommendation for {key}")
        review.append({
            "candidate_id": row.get("candidate_id"),
            "sku": key[0], "field": key[1], "status": row.get("status"),
            "source_hash": row.get("source_hash"), "guard_reasons": row.get("guard_result", {}).get("reasons", []),
            "parsed_candidate": row.get("parsed_candidate"),
            "ai_disposition": recommendation["disposition"],
            "recommended_value": recommendation["recommended_value"],
            "rationale": recommendation["rationale"],
            "owner_approval_required": True,
        })
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "stage5_canary_ai_review_18.json"
    csv_path = args.output_dir / "stage5_canary_ai_review_18.csv"
    json_path.write_text(json.dumps({
        "review_version": "stage5-canary-ai-review-v1",
        "source": str(args.input.resolve()),
        "reviewed_rows": len(review),
        "counts": {
            "ACCEPT_AS_IS": sum(x["ai_disposition"] == "ACCEPT_AS_IS" for x in review),
            "ACCEPT_WITH_GUARD_NOTE": sum(x["ai_disposition"] == "ACCEPT_WITH_GUARD_NOTE" for x in review),
            "MINOR_EDIT": sum(x["ai_disposition"] == "MINOR_EDIT" for x in review),
            "RETRY_MODEL": sum(x["ai_disposition"] == "RETRY_MODEL" for x in review),
        },
        "owner_approval_required": True,
        "production_write": False,
        "rows": review,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    columns = ["candidate_id", "sku", "field", "status", "source_hash", "guard_reasons", "parsed_candidate", "ai_disposition", "recommended_value", "rationale", "owner_approval_required"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for item in review:
            writer.writerow({**item, "guard_reasons": ",".join(item["guard_reasons"])})
    print(json.dumps({"json": str(json_path.resolve()), "csv": str(csv_path.resolve()), "rows": len(review)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
