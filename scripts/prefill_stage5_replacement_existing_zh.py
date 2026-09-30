"""Translate residual Spanish description/detail values in the replacement Gold prefill."""
from __future__ import annotations

import argparse, csv, json
from pathlib import Path
from action_tracker.stage5.source_candidate_v2 import source_consistency_flags

T = {
"3206414": ("保温时间更长；软木底座可防止刮伤桌面和其他表面；无论在家、办公室工作还是旅行，这款实用杯子都能让你随时享用喜爱的热饮。", "颜色：绿色、红色、白色、黑色；可用洗碗机清洗：是；可用微波炉加热：否；容量：400ml；含杯耳：是；可用烤箱：否；杯子/玻璃杯类型：非一次性杯子；商品编号：3206414"),
"3206415": ("圣诞主题烘焙模具，耐受最高200°C高温，可用洗碗机清洗；用这款圣诞树造型模具制作节日甜点。", "颜色：金色、灰色、绿色、红色；材质：硅胶；可用洗碗机清洗：是；可耐烤箱：是；商品编号：3206415"),
"3214057": ("闪亮的独立装饰雪花；多种颜色可选。", "颜色：铜色、金色、银色；商品编号：3214057"),
"3214289": ("48幅精美涂色图案；多种主题可选。", "材质：纸；数量：48张；封面：软封面；页数：48页；装订类型：平装；书籍/活动杂志类型：涂色/绘画；商品编号：3214289"),
"3218163": ("带盖设计，让物品整齐收纳并避免外露；通风孔设计；易于提起和移动。这款57L洗衣篮可整齐收纳衣物，配有通风孔、两个坚固提手和盖子，白色或黑色可选。", "颜色：黑色、白色；材质：塑料；防滑：否；带提手：是；含切割部件：否；可移动：否；可折叠：否；防水：是；适用用途：洗衣；商品编号：3218163"),
"3218280": ("可选择小精灵或圣诞老人造型；使用AAA电池（已包含）；适合作为圣诞装饰的补充。", "颜色：多色；材质：聚苯乙烯、聚丙烯（PP）、聚酯纤维；电池型号：AA；适用场景：室内；含安装材料：是；固定方式：夹式固定；所需电池数量：3；含电池：是；主题：圣诞节；供电类型：电池；季节装饰类型：季节装饰；季节：冬季；商品编号：3218280"),
"3218317": ("可旋转，正反两面均有灯光；带定时器；使用3节AA电池（需另购）。", "电池型号：AA；适用场景：户外；含光源：是；安装方式：悬挂；电线长度：1m；电线长度：1；所需电池数量：3；含电池：否；主题：圣诞节；供电类型：电池；季节装饰类型：季节装饰；光源类型：LED灯；季节：冬季；光源数量：120；商品编号：3218317"),
"3218575": ("带可调节定位孔；3个水平泡。该定位尺有6个固定定位孔，标准定位间距为72mm，适用于欧洲电源插座。", "颜色：银色；商品编号：3218575"),
"3218885": ("采用反应釉；陶瓷制品；可用洗碗机清洗；非常适合享用美味早餐。", "颜色：蓝色、灰色、粉色；材质：陶瓷；可用洗碗机清洗：是；可用微波炉加热：是；形状：圆形；餐盘类型：非一次性餐盘；商品编号：3218885"),
"3220244": ("立即增加收纳空间；整洁易用；可折叠，使用灵活。这款智能收纳架可轻松让厨房橱柜更有条理。", "颜色：透明；厨房置物架/支架/分配器类型：多用途置物架；商品编号：3220244"),
"3222129": ("瓷器制品；带浮雕设计；可用洗碗机、微波炉和烤箱。此款瓷质平盘既适合日常使用，也适合优雅餐桌。", "尺寸（含包装）（长×宽×高）：27×27×2.8cm；颜色：白色；材质：瓷器；可用洗碗机清洗：是；可用微波炉加热：是；形状：圆形；图案：条纹；餐盘类型：非一次性餐盘；商品编号：3222129"),
"3222131": ("瓷器制品；带浮雕设计；可用洗碗机、微波炉和烤箱。这款瓷质沙拉碗适合日常使用，也能让餐桌布置更美观。", "尺寸（含包装）（长×宽×高）：23×23×6.6cm；颜色：白色；材质：瓷器；表面处理：亮面；可用洗碗机清洗：是；可用微波炉加热：是；图案：条纹；可耐烤箱：是；商品编号：3222131"),
"3223373": ("带金色边框和提手；在家中格外醒目；适合端放物品或装饰。这款优雅托盘可为餐桌或橱柜增添奢华感。", "颜色：灰褐色、白色、黑色；材质：大理石、MDF木材、铁；形状：圆形；商品编号：3223373"),
"3223768": ("完整套装，含3个冲孔盒；适合打孔和圆角；适用于手工制作和DIY项目。", "颜色：蓝色、绿色、粉色、紫色、黄色；数量：22件；商品编号：3223768"),
"3224857": ("烘烤程度可调；具有解冻和加热功能；配可拆卸面包屑托盘。用这款复古烤面包机开启美好一天，享用烤制完美的面包。", "颜色：黑色、蓝色、白色；含面包屑托盘：是；含指示灯：是；插槽数量：2个插槽；功率：930；功率：930W；供电类型：市电；电压：240；电压：240V；商品编号：3224857"),
"3225743": ("适合DIY爱好者的趣味礼物；每天都有新颖实用的惊喜；用这款降临节日历倒数等待圣诞节。", "数量：24件；商品编号：3225743"),
"3225994": ("柔软的仿麂皮面料；舒适弹性腰部；修身剪裁。这款女士仿麂皮打底裤兼具优雅外观和舒适版型。", "服装尺码：XL；颜色：棕色、黑色；材质：聚酯纤维、弹性纤维；洗涤说明：最高30°C机洗；熨烫说明：不可熨烫；烘干说明：不可滚筒烘干；商品编号：3225994"),
"3226277": ("镀金不锈钢；配有Swarovski水晶；均码。用这款优雅的金色首饰为造型增添亮点。", "颜色：金色；商品编号：3226277"),
}

# The source candidate selector intentionally does not mutate category facts.
# These are only Chinese derived-field repairs for rows where the existing
# category dictionary value was still Spanish.  The Spanish source columns
# remain untouched in the output.
CAT2_ZH = {
    "3206415": "烤盘和烘焙托盘",
    "3214057": "手工装饰",
    "3214289": "涂色和绘画",
    "3218280": "家居装饰",
    "3218317": "家居装饰",
    "3222129": "餐具",
    "3222131": "餐具",
    "3223373": "家居装饰",
    "3224814": "宠物玩具",
    "3225347": "家居装饰",
}

def main(source: Path, output: Path) -> dict[str, int]:
    with source.open(encoding="utf-8-sig", newline="") as handle: rows = list(csv.DictReader(handle))
    changed = 0
    for row in rows:
        if row["sku"] in T:
            row["description_zh"], row["details_zh"] = T[row["sku"]]; changed += 2
        if row["sku"] in CAT2_ZH:
            row["cat2_zh"] = CAT2_ZH[row["sku"]]
        source = {"name": row.get("name_es", ""), "cat1": row.get("cat1_es", ""),
                  "cat2": row.get("cat2_es", ""), "spec": row.get("spec_es", ""),
                  "description": row.get("description_es", ""), "details": row.get("details_es", "")}
        row["candidate_status"] = "SOURCE_CONFLICT_REVIEW" if source_consistency_flags(source) else "SOURCE_CANDIDATE_READY"
        row["missing_zh_fields"] = "|".join(field for field in ("description_zh", "details_zh") if not row.get(field))
    output.parent.mkdir(parents=True, exist_ok=True)
    columns = list(rows[0]) if rows else ["sku"]
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns); writer.writeheader(); writer.writerows(rows)
    ready = [r for r in rows if r["candidate_status"] == "SOURCE_CANDIDATE_READY"]
    conflict = [r for r in rows if r["candidate_status"] == "SOURCE_CONFLICT_REVIEW"]
    ready_path = output.with_name("stage5_replacement_gold_ready_25.csv")
    conflict_path = output.with_name("stage5_replacement_source_conflict_review_11.csv")
    for split_path, split_rows in ((ready_path, ready), (conflict_path, conflict)):
        with split_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns); writer.writeheader(); writer.writerows(split_rows)
    report_path = output.with_name("stage5_replacement_gold_prefill_report.md")
    report_path.write_text(
        "# Stage 5 replacement Gold prefill\n\n"
        f"- Source candidates: {len(rows)}\n- Ready after consistency gate: {len(ready)}\n"
        f"- Source-conflict review: {len(conflict)}\n- Missing Chinese fields: {sum(bool(r['missing_zh_fields']) for r in rows)}\n"
        "- Owner decisions: 0\n- Training runs: 0\n- Production writes: 0\n\n"
        "The 11 conflict rows are retained for review and are not eligible for automatic Gold or training.\n",
        encoding="utf-8",
    )
    manifest = {"source_count": len(rows), "translated_field_count": changed, "category_repairs": len(CAT2_ZH), "remaining_missing_field_count": sum(bool(r["missing_zh_fields"]) for r in rows), "source_conflict_review_count": len(conflict), "source_candidate_ready_count": len(ready), "owner_decisions": 0, "training_runs": 0, "production_writes": 0, "status": "OWNER_REVIEW_REQUIRED", "csv": str(output), "ready_csv": str(ready_path), "conflict_review_csv": str(conflict_path), "report_md": str(report_path)}
    output.with_name("stage5_replacement_gold_prefill_final_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest

if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--source", default="runtime/training/qwen3_8b/20260915/stage5_replacement_source_versions_119/stage5_replacement_gold_prefill_ai.csv"); parser.add_argument("--output", default="runtime/training/qwen3_8b/20260915/stage5_replacement_source_versions_119/stage5_replacement_gold_prefill_final.csv"); args = parser.parse_args(); print(json.dumps(main(Path(args.source), Path(args.output)), ensure_ascii=False, indent=2))
