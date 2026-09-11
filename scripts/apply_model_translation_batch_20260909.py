"""Apply the model-authored Chinese completion batch for the current 22 SKUs.

Only Chinese derived fields are changed.  Spanish facts, prices, URLs,
lifecycle and the missing official second category remain untouched.  Each
changed field gets an immutable patch revision and field-level provenance.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from action_tracker.config import load_settings
from action_tracker.database.connection import connect
from action_tracker.database.integration import database_path, regenerate_compatibility_exports
from action_tracker.database.patches import append_patch_event, create_patch
from action_tracker.database.production import backup_database
from action_tracker.database.provenance import sync_localization_field_provenance


TRANSLATIONS = {
    "3221992": {
        "name": "天鹅绒彩色海报", "cat1": "兴趣手作", "spec": "40×60cm｜多种款式",
        "description": "内含10支彩色马克笔\n可选择你喜欢的款式！",
        "details": "商品编号：3221992",
    },
    "3222222": {
        "name": "奶酪零食", "cat1": "食品饮料", "spec": "130g｜多种款式",
        "description": "浓郁奶酪风味\n蝴蝶形或奶酪棒形酥皮小点心\n搭配高达奶酪，适合作为佐酒小食或咸味零食\n享用美味的奶酪蝴蝶酥或奶酪棒。这些酥脆的酥皮点心含有高达奶酪，适合佐酒或作为咸味零食。",
        "details": "碳水化合物：44.7g; 储存建议：置于阴凉干燥处保存; 含量：130g; 其中糖：1.4g; 其中饱和脂肪：16g; 能量：559kcal; 脂肪：36g; 蛋白质：12g; 口味：奶酪; 盐：2.25g; 商品编号：3222222",
    },
    "3222383": {
        "name": "纸盘", "cat1": "兴趣手作", "spec": "10个｜直径23cm｜多种款式",
        "description": "立即营造节日氛围\n方便保持桌面整洁\n适合各种派对风格\n用这些印花纸盘为派对增添欢乐气氛。适合放置蛋糕、开胃小食或儿童派对中的小吃。使用后清理方便，非常适合喜欢让餐桌兼具便利与色彩的人。",
        "details": "颜色：图案; 材质：纸; 数量：10个; 形状：圆形; 印刷：是; 一次性餐具类型：一次性纸盘; 商品编号：3222383",
    },
    "3222807": {
        "name": "仿石边框相框", "cat1": "家居布置", "spec": "12.5×17cm｜多种款式",
        "description": "适用于10×15cm照片\n自然风格\n给喜欢的照片一个特别的位置。这款仿石边框相框造型有趣，带有石材质感边框，呈现自然现代的气息。适合摆放美好回忆、旅行照片或亲友照片。可将相框放在柜子、搁板或书桌上，为家中增添个性。",
        "details": "颜色：灰色、粉色、灰褐色; 可放多张照片：1; 带玻璃：是; 安装方式：立式摆放; 相框类型：是; 商品编号：3222807",
    },
    "3222971": {
        "name": "钢化玻璃屏幕保护膜", "cat1": "数码影音", "spec": "2个｜适用于iPhone｜多种款式",
        "description": "9H抗冲击玻璃，可保护手机屏幕\n可承受高达2米高度跌落\n超薄设计，带蓝光过滤和卫生涂层\n用这款玻璃屏幕保护膜为iPhone提供最大程度的保护。即使手机意外掉落在瓷砖等坚硬表面上，也能降低屏幕刮花或破裂的概率。保护膜可能会损坏，但你的设备屏幕仍能得到保护。包装内含两个保护膜，方便及时更换。随附安装器，贴膜非常简单，让你外出使用手机时更安心。\n更可持续的选择\n本产品所用材料至少含有50%的再生材料。\n符合全球回收标准（GRS）认证的产品，其再生材料在供应链各环节均经过独立验证，从原料来源到最终产品均有追溯；从原料来源到最终供应商的生产设施也符合相关社会、环境和化学要求。",
        "details": "颜色：透明; 适用智能手机类型：Apple; 数量：2个; 卡槽：否; 商品编号：3222971",
    },
    "3223281": {
        "name": "狗狗零食", "cat1": "宠物用品", "spec": "100g｜多种款式",
        "description": "鸡肉或鸭肉口味\n美味零食，每天都能给爱犬解馋\n方便的可重新封口包装，长时间保持新鲜\n用这款狗狗零食宠爱你的爱犬。这些柔软的鸡肉或鸭肉小块易于作为加餐或奖励，也能为日常饮食增添美味。",
        "details": "适用宠物类型：狗; 储存建议：置于阴凉干燥处保存; 含量：100g; 包装：是; 包装形式：袋装; 口味：鸭肉、鸡肉; 商品编号：3223281",
    },
    "3223283": {
        "name": "狗狗咀嚼零食", "cat1": "宠物用品", "spec": "100g｜多种款式",
        "description": "可选择鸡肉或鸭肉口味\n耐嚼，带来持久乐趣\n富含蛋白质\n用这款狗狗零食宠爱你的爱犬。牛皮咀嚼零食外层包裹鸡肉或鸭肉，带来持久的咀嚼乐趣，也能为日常饮食增添美味。",
        "details": "适用宠物类型：狗; 储存建议：置于阴凉干燥处保存; 含量：100g; 包装：是; 包装形式：袋装; 不含防腐剂：否; 宠物食品类型：混合型; 宠物饲料类型：零食; 商品编号：3223283",
    },
    "3223312": {
        "name": "旋转螺丝刀", "cat1": "DIY五金", "spec": "4V",
        "description": "内含5种不同批头\n可通过USB-C线充电\n配有坚固收纳盒\n紧凑型电动螺丝刀，配有实用收纳盒。适合在家进行小型维修工作。角度可调，开箱即可使用。",
        "details": "颜色：灰色; 含收纳盒：是; 可充电：是; 电压：4; 电压：4V; 商品编号：3223312",
    },
    "3223316": {
        "name": "猫抓板", "cat1": "宠物用品", "spec": "27.5×27.5×38cm｜多种颜色",
        "description": "含猫薄荷\n培养抓挠习惯\n保护家具\n给猫咪一个属于自己的小角落。这款猫抓板将躲藏空间与纸板抓挠面结合在一起，并配有猫薄荷，让玩耍更加有趣。",
        "details": "颜色：蓝色、粉色; 材质：纸板; 适用宠物类型：猫; 适用场景：室内; 形状：方形; 含声音功能：否; 防水：否; 宠物非电动玩具类型：猫抓柱/宠物抓挠面; 商品编号：3223316",
    },
    "3224121": {
        "name": "Fluffy Whirl软泥", "cat1": "玩具", "spec": "多种款式",
        "description": "内含闪粉\n带有宜人香味\n蓬松有弹性的软泥\n和Fluffy Whirl软泥一起揉捏玩耍。这款柔软有弹性的软泥会形成有趣的旋涡，气味怡人。适合揉捏、拉伸和进行创意游戏。",
        "details": "适用年龄：3岁以上; 商品编号：3224121",
    },
    "3224146": {
        "name": "辣味鸡肉韩式炒年糕", "cat1": "食品饮料", "spec": "182g",
        "description": "制作快速简单\n口感柔软的热门韩式小吃\n用这款辣味鸡肉韩式炒年糕享受韩国风味。米饼裹着香辣酱汁，是快速用餐或咸味小吃的理想选择。",
        "details": "碳水化合物：49.8g; 储存建议：置于阴凉干燥处保存; 含量：182g; 其中糖：3.8g; 其中饱和脂肪：1.1g; 能量：269kcal; 脂肪：4.5g; 蛋白质：4.3g; 盐：0.62g; 商品编号：3224146",
    },
    "3224814": {
        "name": "狗狗礼品套装", "cat1": "宠物用品", "spec": "5件",
        "description": "长时间玩乐\n每天都有新挑战\n让爱犬保持活力\n给爱犬一个惊喜。这套礼品包含毛绒骨头、磨牙绳、网球和狗狗零食。品类丰富，狗狗既可以玩耍、啃咬，也能获得奖励。",
        "details": "颜色：多色; 适用宠物类型：狗; 数量：4件; 商品编号：3224814",
    },
    "3224907": {
        "name": "夹式蝴蝶装饰", "cat1": "家居布置", "spec": "3个｜多种颜色",
        "description": "使用夹子即可轻松固定\n闪亮细节，营造奢华效果\n用这些蝴蝶装饰为圣诞布置增添俏皮又优雅的气息。闪亮细节和柔和色彩带来醒目奢华的效果。借助实用夹子，可轻松固定在圣诞树、花环或其他装饰物上，快速增添节日氛围和独特风格。",
        "details": "颜色：金色、粉色、红色、白色; 材质：聚酯纤维、铁; 数量：3个; 含安装配件：是; 安装方式：悬挂; 固定方式：悬挂; 主题：圣诞节; 季节装饰类型：季节装饰; 季节：冬季; 商品编号：3224907",
    },
    "3225118": {
        "name": "仿木纹边桌套装", "cat1": "家居布置", "spec": "2件｜多种颜色",
        "description": "两种尺寸：直径35cm或38cm\n适合各种家居环境\n最大承重4/5kg\n这套两件套边桌为家居增添优雅气息。可以一起使用，也可以分开摆放，瞬间为客厅增添现代感。",
        "details": "颜色：棕色; 数量：2件; 桌子类型：边桌; 商品编号：3225118",
    },
    "3225230": {
        "name": "LED圣诞树装饰灯", "cat1": "家居布置", "spec": "直径6×13cm｜多种颜色",
        "description": "装饰性玻璃圣诞树\n配有LED灯，发出温暖光线\n用这棵玻璃圣诞树为家中营造氛围。柔和的LED灯光和小巧尺寸使其适合摆放在各种位置，是营造温馨圣诞氛围的理想装饰。",
        "details": "颜色：棕色、金色、绿色、红色; 材质：玻璃; 电池型号：LR44; 适用场景：室内; 含光源：是; 安装方式：立式摆放; 固定方式：独立摆放; 所需电池数量：3; 含电池：是; 主题：圣诞节; 供电方式：电池; 季节装饰类型：季节装饰; 光源类型：LED灯; 季节：冬季; 商品编号：3225230",
    },
    "3225347": {
        "name": "硅胶烘焙模具", "cat1": "厨房餐具", "spec": "多种款式",
        "description": "可在最高230°C烤箱中使用\n易于脱模，成品效果更佳\n适合节日使用\n用这款欢乐的硅胶模具让烘焙更有趣！可将面糊变成醒目的圣诞主题蛋糕。适合在圣诞假期享受愉快的烘焙时光，脱模轻松，成品漂亮又有趣。",
        "details": "颜色：蓝色、金色、绿色、红色; 材质：硅胶; 可放入洗碗机：是; 可用于烤箱：是; 烘焙/烤箱/烧烤设备类型：蛋糕模具（非一次性）; 商品编号：3225347",
    },
    "3225602": {
        "name": "圣诞桌布", "cat1": "厨房餐具", "spec": "140×240cm｜多种款式",
        "description": "节日图案\n柔和光泽，营造奢华效果\n立即为餐桌增添节日气息。这款优雅的金银丝桌布为晚餐、午餐以及与家人朋友共度的时光营造温暖的圣诞氛围。细腻光泽为餐桌增添奢华感而不过分。非常适合完成圣诞布置，为各种节日场合增添氛围。",
        "details": "颜色：金色、灰色、白色; 适用家具类型：桌子; 洗涤说明：最高40°C机洗; 熨烫说明：最高110°C熨烫; 可洗涤：是; 商品编号：3225602",
    },
    "3225667": {
        "name": "女童居家袜", "cat1": "服饰鞋包", "spec": "2双｜23-34码｜多种款式",
        "description": "柔软蓬松的面料\n保暖舒适\n可选择迪士尼史迪奇、米老鼠或Hello Kitty图案\n穿上这款柔软的居家袜，让双脚保持温暖。袜子采用活泼的人物图案，穿着舒适，非常适合在寒冷天气里居家使用。",
        "details": "袜子尺码：27-30、31-34; 颜色：图案; 材质：聚酯纤维、聚酰胺、弹性纤维; 数量：2双; 性别：女; 洗涤说明：最高30°C机洗; 熨烫说明：不可熨烫; 烘干说明：不可滚筒烘干; 袜筒长度：小腿中筒; 商品编号：3225667",
    },
    "3225894": {
        "name": "蝴蝶结烛台", "cat1": "家居布置", "spec": "12×8×6cm｜多种颜色",
        "description": "适合晚餐时摆放蜡烛\n适合作为餐桌装饰\n可爱的蝴蝶结造型\n这款蝴蝶结烛台能为餐桌增添俏皮又优雅的气息。闪亮的蝴蝶结造型醒目别致，适合节日时刻或作为室内装饰。",
        "details": "颜色：粉色、红色、白色; 材质：陶瓷; 适用蜡烛类型：餐桌蜡烛; 可放多支蜡烛：1; 含蜡烛：否; 商品编号：3225894",
    },
    "3225959": {
        "name": "发夹", "cat1": "服饰鞋包", "spec": "16个｜多种颜色",
        "description": "帮助将头发固定在脸部之外\n适合打造各种有趣发型\n多款发夹可自由搭配\n这些发夹可以快速轻松地为造型增添风格。使用弹簧夹将头发固定在脸部之外，适合工作、上学或旅行时使用。还可以灵活搭配，打造不断变化的有趣发型。既可以低调使用，也可以作为醒目的发型点缀。",
        "details": "颜色：多色、黑色; 材质：铁; 数量：16个; 性别：女; 商品编号：3225959",
    },
    "3226091": {
        "name": "芒果葡萄味软糖棒", "cat1": "食品饮料", "spec": "120g",
        "description": "芒果和葡萄口味的软糖棒\n芒果和葡萄风味\n独立包装\n享用这些芒果和葡萄口味的迷你软糖棒。柔软糖果做成小棒棒糖形状，并采用独立包装，方便分享或随身携带。",
        "details": "碳水化合物：84g; 储存建议：置于阴凉干燥处保存; 含量：120g; 其中糖：50g; 其中饱和脂肪：0g; 能量：342kcal; 脂肪：0g; 清真：是; 蛋白质：0g; 盐：0.29g; 素食：否; 商品编号：3226091",
    },
    "3227975": {
        "name": "多用途清洁喷雾", "cat1": "家务清洁", "spec": "750ml",
        "description": "强效去除油脂、污垢和污渍\n清洁后不留痕迹\n深层清洁\n使用这款多用途清洁喷雾轻松清洁各种表面。配方适用于玻璃、镜子、瓷砖、家具和家电，并能留下明亮无痕的表面效果。",
        "details": "含量：750ml; 清洁功能：是; 目标表面材质：玻璃; 物质形态：液体/喷雾; 分配器类型：喷雾; 商品编号：3227975",
    },
}


def main() -> None:
    cfg = load_settings()
    db_path = database_path(cfg)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = Path(cfg["paths"]["backups"]) / f"action_tracker_before_model_translation_{stamp}.db"
    backup_database(db_path, backup)
    import_id = f"model-translation-{stamp}"
    now = datetime.now(timezone.utc).isoformat()
    changed = 0
    changed_fields = 0
    with connect(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        for sku, translated in TRANSLATIONS.items():
            es = db.execute(
                "SELECT source_hash FROM product_localizations WHERE official_sku=? AND language='es'", (sku,)
            ).fetchone()
            zh = db.execute(
                "SELECT name,cat1,cat2,spec,description,details FROM product_localizations "
                "WHERE official_sku=? AND language='zh'", (sku,)
            ).fetchone()
            if not es or not zh:
                raise RuntimeError(f"LOCALIZATION_ROW_MISSING:{sku}")
            source_hash = str(es[0] or "")
            old = dict(zip(("name", "cat1", "cat2", "spec", "description", "details"), zh))
            # Category 2 has no Spanish source fact for these rows.  It stays
            # null and remains in the official category backlog.
            merged = {**old, **translated, "cat2": old["cat2"]}
            field_status = {field: "APPROVED" for field in translated}
            field_status["cat2"] = "PENDING"
            field_source = {field: "MODEL_GPT_5.6_LUNA" for field in translated}
            field_source["cat2"] = "OFFICIAL_CATEGORY_PENDING"
            db.execute(
                """UPDATE product_localizations SET name=?,cat1=?,cat2=?,spec=?,description=?,details=?,
                source=?,review_status=?,resolution_status=?,name_source=?,cat1_source=?,cat2_source=?,
                spec_source=?,description_source=?,details_source=?,freshness_status=?,approved_by=?,approved_at=?,
                applied_commit_id=?,updated_at=? WHERE official_sku=? AND language='zh'""",
                (merged["name"], merged["cat1"], merged["cat2"], merged["spec"], merged["description"], merged["details"],
                 "MODEL_GPT_5.6_LUNA", "PARTIAL_PENDING_CATEGORY", "MODEL_TRANSLATED_PARTIAL",
                 field_source["name"], field_source["cat1"], field_source["cat2"], field_source["spec"],
                 field_source["description"], field_source["details"], "CURRENT", "user-requested-model-translation",
                 now, import_id, now, sku),
            )
            db.execute("UPDATE products SET name_zh=?,updated_at=? WHERE official_sku=?", (merged["name"], now, sku))
            sync_row = {"sku": sku, "language": "zh", **merged, "source": "MODEL_GPT_5.6_LUNA",
                        "review_status": "PARTIAL_PENDING_CATEGORY", "source_hash": source_hash,
                        "applied_commit_id": import_id, "freshness_status": "CURRENT"}
            for field in translated:
                sync_row[f"{field}_source"] = field_source[field]
                sync_row[f"{field}_review_status"] = field_status[field]
            sync_row["cat2_source"] = field_source["cat2"]
            sync_row["cat2_review_status"] = "PENDING"
            sync_localization_field_provenance(db, sync_row, commit_id=import_id, now=now)
            for field, new_value in translated.items():
                if old[field] == new_value:
                    continue
                patch_id = create_patch(
                    db, official_sku=sku, language="zh", field_name=field,
                    old_value=old[field], new_value=new_value, source_hash=source_hash,
                    reason="user_requested_model_translation", created_by="GPT-5.6-LUNA",
                )
                append_patch_event(db, patch_id, "PATCH_APPROVED", actor="user-requested-model-translation", reason="用户授权模型翻译")
                append_patch_event(db, patch_id, "PATCH_APPLIED", actor="GPT-5.6-LUNA", reason="中文字段写入 SQLite PRIMARY")
                changed_fields += 1
            changed += 1
            # Close the old broad translation request and leave only the
            # genuinely unresolved official category field pending.
            db.execute(
                "UPDATE translation_queue SET status='COMPLETED',completed_at=?,last_error=NULL WHERE official_sku=? AND language='zh' AND status!='COMPLETED'",
                (now, sku),
            )
            fields_json = json.dumps(["cat2"], ensure_ascii=False, separators=(",", ":"))
            queue_id = hashlib.sha256(f"{sku}|zh|{source_hash}|{fields_json}".encode()).hexdigest()
            db.execute(
                """INSERT INTO translation_queue(queue_id,official_sku,language,source_hash,requested_fields,reason,priority,status,retry_count,run_id,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(official_sku,language,source_hash,requested_fields) DO UPDATE SET
                  reason=excluded.reason,priority=excluded.priority,status='PENDING',run_id=excluded.run_id""",
                (queue_id, sku, "zh", source_hash, fields_json, "OFFICIAL_CATEGORY_MISSING", "NORMAL", "PENDING", 0, import_id, now),
            )
        db.commit()
    head = __import__("action_tracker.database.repository", fromlist=["ProductionRepository"]).ProductionRepository(db_path).current_head()
    sync = regenerate_compatibility_exports(cfg, head) if head else None
    report = {"import_id": import_id, "backup": str(backup), "changed_skus": changed,
              "changed_fields": changed_fields, "remaining_category_pending": len(TRANSLATIONS),
              "master_sync": sync, "model": "GPT-5.6-LUNA", "finished_at": now}
    output = Path(cfg["paths"]["logs"]) / f"model_translation_batch_{stamp}.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({**report, "report": str(output)}, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
