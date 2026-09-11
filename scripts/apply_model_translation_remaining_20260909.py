"""Apply model-authored Chinese translations for the remaining active queue.

The source facts are read from SQLite PRIMARY.  This script only changes the
Chinese projection and its field-level provenance.  Spanish facts, prices,
URLs, lifecycle state, and source categories are not changed.
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


# These translations are authored directly by the model from the Spanish
# source fields.  Product/series tokens are kept only where they identify the
# product itself; no "牌" brand suffix is introduced.
TRANSLATIONS = {
    "3206734": {
        "name": "雪景球", "cat1": "家居布置", "cat2": "家居装饰", "spec": "10×13cm｜多种款式",
        "description": "终极圣诞装饰\n完善你的收藏！\n摇动雪景球，欣赏梦幻的冬日仙境。",
        "details": "颜色：多色; 材质：玻璃、聚氨酯树脂; 适用场景：室内; 形状：圆形; 安装方式：立式摆放; 主题：圣诞节; 季节装饰类型：季节装饰; 季节：冬季; 商品编号：3206734",
    },
    "3011467": {
        "name": "泡澡球", "cat1": "个人美容", "cat2": "沐浴和淋浴用品", "spec": "多种款式",
        "description": "纯素产品\n带有美妙香气和梦幻色彩\n用这款泡澡球，让泡澡乐趣爆棚。",
        "details": "颜色：蓝色、多色、粉色; 含量：100g; 适用场景：泡澡; 商品编号：3011467",
    },
    "2558074": {
        "name": "圣诞巧克力夹心糖", "cat1": "食品饮料", "cat2": "巧克力", "spec": "86g",
        "description": "独立包装\n阿尔卑斯牛奶巧克力\n美味糖果将柔滑牛奶巧克力与酥脆奥利奥饼干结合。",
        "details": "碳水化合物：55g; 巧克力/糖组合：含其他配料或夹心的巧克力; 储存建议：置于阴凉干燥处保存; 含量：86g; 其中糖：53g; 其中饱和脂肪：20g; 能量：565kcal; 膳食纤维：1.2g; 形状：图案; 脂肪：36g; 适合特殊场合：是; 蛋白质：5.2g; 口味：巧克力; 盐：0.30g; 混合装：是，单一类型; 商品编号：2558074",
    },
    "3214942": {
        "name": "薄荷糖", "cat1": "食品饮料", "cat2": "糖果", "spec": "100g",
        "description": "70颗\n无糖\n让口气保持清新",
        "details": "数量：100颗; 碳水化合物：98.2g; 储存建议：置于阴凉干燥处保存; 含量：70g; 其中糖：0.2g; 其中饱和脂肪：0.6g; 能量：242kcal; 脂肪：0.7g; 蛋白质：0g; 口味：薄荷; 盐：0.01g; 商品编号：3214942",
    },
    "3217468": {
        "name": "圣诞小店DIY套装", "cat1": "兴趣手作", "cat2": "手工制作", "spec": "多种款式",
        "description": "有趣的圣诞活动\n带灯光（含电池）\nFSC®认证木材：可持续木材\n用这套漂亮的套装打造自己的圣诞小店，配有氛围灯。适合一个创意下午，也非常适合作为圣诞村或节日装饰的一部分。",
        "details": "颜色：多色; 商品编号：3217468",
    },
    "3213525": {
        "name": "指甲护理液", "cat1": "个人美容", "cat2": "美甲", "spec": "10ml｜多种款式",
        "description": "呵护指甲和角质层\n用实用滴管轻松涂抹一滴\n纯素配方",
        "details": "颜色：透明; 含量：11ml; 含UV灯：否; 需要UV灯：否; 分配器类型：滴管; 商品编号：3213525",
    },
    "3218300": {
        "name": "液体腮红双支装", "cat1": "个人美容", "cat2": "彩妆", "spec": "2×2ml｜多种颜色",
        "description": "让面部呈现清新健康的气色\n细头涂抹器，方便精准上妆\n可用手指或化妆刷轻松晕染\n这款液体腮红双支装，只需少量即可为面部带来健康光泽。用细头涂抹器取少量产品，再用手指或化妆刷晕染。适合日常打造自然妆容，也适合外出时增添一抹色彩。液体腮红上妆快速、易于晕染，方便随身携带或快速补妆。",
        "details": "颜色：多色; 适用面部部位：脸颊; 含镜子：否; 含量：4ml; 分配器类型：棒状; 商品编号：3218300",
    },
    "2548558": {
        # The official page has no independent specification summary.  Keep
        # the existing translated name/categories and leave spec empty; the
        # queue is closed as an official-source absence below.
        "name": "万圣节小灯笼", "cat1": "家居布置", "cat2": "家居装饰",
    },
    "3219090": {
        "name": "羊毛被套", "cat1": "家居布置", "cat2": "床上用品", "spec": "155×220cm｜多种款式",
        "description": "含50×80cm枕套\n漂亮的印花图案\n拉链闭合\n这款带有活泼印花的羊毛被套，让入睡变成一场欢乐体验。保暖舒适，适合寒冷夜晚，也非常适合喜欢做梦的孩子。",
        "details": "颜色：图案; 材质：聚酯纤维; 数量：2件; 含枕套：是; 洗涤说明：最高30°C机洗; 熨烫说明：不可熨烫; 烘干说明：不可滚筒烘干; 闭合方式：拉链; 纺织品类型：羊毛; 商品编号：3219090",
    },
    "3221215": {
        "name": "圣诞巧克力饼干", "cat1": "食品饮料", "cat2": "饼干", "spec": "18块",
        "description": "星形饼干\n含可可夹心\n口感酥脆\n本产品获得公平贸易认证。合作的可可种植者可获得公平的基础价格和额外溢价。公平贸易体系能在许多方面显著改善种植者及其家人的生活，也让巧克力更加美味。",
        "details": "数量：18块; 碳水化合物：65.1g; 含量：162g; 其中糖：33.6g; 其中饱和脂肪：14.2g; 能量：507kcal; 形状：图案; 脂肪：24g; 蛋白质：6.9g; 口味：巧克力; 盐：0.4g; 无糖：否; 无麸质：否; 商品编号：3221215",
    },
    "3218150": {
        "name": "怪兽爪", "cat1": "兴趣手作", "cat2": "派对用品", "spec": "多种款式",
        "description": "带灯光和音效\n可伸缩爪子\n大胆造型\n只需把怪兽爪戴在手上并启动灯光和音效，就能在奇幻冒险中获得更多乐趣。",
        "details": "材质：塑料; 适用年龄：3岁以上; 商品编号：3218150",
    },
    "3215546": {
        "name": "高光粉", "cat1": "个人美容", "cat2": "彩妆", "spec": "多种颜色",
        "description": "打造柔和明亮的妆效\n带有3D效果\n纯素配方\n用这款3D高光粉让肌肤焕发光彩。丝滑配方带来漂亮的光泽。",
        "details": "含镜子：否; 含量：6g; 效果：光泽; 含化妆刷：否; 物质形态：粉末—压缩粉饼; 面部彩妆类型：高光粉; 商品编号：3215546",
    },
    "3220881": {
        "name": "玻璃花瓶", "cat1": "家居布置", "cat2": "家居装饰", "spec": "直径14×19cm",
        "description": "带有波纹玻璃\n优雅的玻璃花瓶，带有细腻浮雕和优美造型。非常适合插花，也可以作为家居装饰。经典设计适合各种室内环境。",
        "details": "颜色：透明; 材质：玻璃; 形状：沙漏形; 含安装配件：否; 含支架：否; 商品编号：3220881",
    },
    "3221470": {
        "name": "Glow Pong游戏套装", "cat1": "玩具", "cat2": "游戏", "spec": "多种颜色",
        "description": "灯光效果，营造更浓厚的节日氛围\n完整套装，打开即可玩\n适合派对和游戏之夜\n用这款Glow Pong游戏套装让每场派对更加有趣！杯子和发光棒在黑暗中营造出壮观的灯光效果。挑战朋友，享受数小时欢乐。安装简单，适合室内或户外使用。非常适合派对、游戏之夜，或单纯享受一场有趣的挑战。让灯光游戏开始吧！",
        "details": "颜色：多色; 适用年龄：3岁以上; 数量：42件; 含球：是; 商品编号：3221470",
    },
    "3219204": {
        "name": "UV LED美甲灯", "cat1": "个人美容", "cat2": "美甲", "spec": "48W",
        "description": "四种模式，包括低热模式，可精准控制凝胶指甲油固化\n三种最长120秒的定时器，控制更加精准\n配有手机支架\n使用这款UV LED美甲灯，在家打造自己的美甲沙龙。适用于凝胶指甲油和美甲艺术。四种模式中包含低热模式，配合三种最长120秒的实用定时器，让你始终掌握固化过程。还配有手机支架，美甲时可以观看教程或追剧。适合快速修饰或完成完整美甲。",
        "details": "含包装尺寸（长×宽×高）：19.2×21.1×11cm; 颜色：青铜色; 含UV灯：是; 功率：48; 功率：48W; 电压：240; 电压：240V; 商品编号：3219204",
    },
    "3213693": {
        "name": "混合饼干", "cat1": "食品饮料", "cat2": "饼干", "spec": "200g",
        "description": "美味混合饼干\n采用雨林联盟认证可可制成：对人类和环境都有益\n适合来客时分享，也适合在家与亲友共度愉快时光。",
        "details": "碳水化合物：62.3g; 储存建议：置于阴凉干燥处保存; 含量：200g; 其中糖：32.9g; 其中饱和脂肪：13.9g; 能量：513kcal; 膳食纤维：2.5g; 脂肪：25.8g; 蛋白质：6.4g; 盐：0.25g; 无糖：否; 商品编号：3213693",
    },
    "3216011": {
        "name": "筋膜枪", "cat1": "运动用品", "cat2": "身体护理", "spec": "6件",
        "description": "深入放松肌肉\n配有5种不同按摩头\n可通过USB-C线充电（需另购）\n忙碌一天后，用这款粉色电动筋膜枪呵护肌肉。配有多个配件和多档速度，可随时随地进行深层肌肉按摩。",
        "details": "颜色：粉色; 供电方式：电池; 商品编号：3216011",
    },
    "3012006": {
        "name": "仿真玫瑰", "cat1": "家居布置", "cat2": "家居装饰", "spec": "多种款式",
        "description": "提供多种浪漫色彩\n用这束仿真玫瑰为餐桌或窗台营造更温馨的氛围。",
        "details": "颜色：红色、粉色、白色; 花盆颜色：黑色; 花盆表面处理：光滑; 花盆形状：圆形; 含花盆：是; 植物类型：玫瑰; 塑料植物类型：仿真花; 商品编号：3012006",
    },
    "3215954": {
        "name": "平底锅", "cat1": "厨房餐具", "cat2": "煎锅", "spec": "直径24cm",
        "description": "不锈钢锅柄\n适用于所有热源\n高品质ILAG不粘涂层\n这款直径24cm的不锈钢平底锅坚固耐用，适合日常烹饪。高品质ILAG不粘涂层可防止食材粘锅，让你用更少的油烹饪。适用于包括电磁炉在内的所有热源，并配有舒适的不锈钢手柄。",
        "details": "颜色：银色; 材质：铝、不锈钢; 可放入洗碗机：否; 可用于微波炉：否; 适用热源：燃气、电、陶瓷、电磁炉; 带容量刻度：否; 含锅盖：否; 带导流嘴：否; 手柄材质：不锈钢; 可用于烤箱：是; 商品编号：3215954",
    },
    "3213541": {
        "name": "唇膜套装", "cat1": "个人美容", "cat2": "面部护理", "spec": "2件",
        "description": "让双唇柔软有弹性\n含蜂蜜\n附带小勺\n用这款唇膜套装呵护双唇。内含2片滋养型唇膜，可深层滋润并柔软双唇。",
        "details": "数量：2件; 含量：10.4g; 保湿效果：是; 滋养效果：是; 口味：蜂蜜; 无香型：否; 商品编号：3213541",
    },
    "2557704": {
        "name": "汽水", "cat1": "食品饮料", "cat2": "饮料",
    },
    "3217273": {
        "name": "藤条香薰", "cat1": "家居布置", "cat2": "家居装饰", "spec": "300ml｜多种款式",
        "description": "奢华香薰瓶\n可选择多种怡人香味\n这款香薰扩香器不仅能为家中带来宜人香气，也能成为各种室内环境中的亮眼装饰。",
        "details": "含量：300ml; 含香薰藤条：是; 含替换装：否; 瓶身材质：玻璃; 可重新填充：否; 物质形态：液体; 分配器类型：瓶装; 商品编号：3217273",
    },
    # Historical/offline rows are included only when source facts exist.  A
    # missing official source field is left unchanged and recorded as such.
    "3214938": {},
    "3205548": {
        "name": "发蜡棒", "cat1": "个人美容", "cat2": "头发护理", "spec": "75g｜多种香味",
        "description": "打造顺滑、柔软且不毛躁的固定发型\n哑光效果\n强效发蜡\n这款发蜡棒非常适合打造牢固持久的发型，例如没有碎发的整洁发髻。棒状设计便于精准涂抹，可针对发梢或需要额外整理的发束进行造型。",
        "details": "需冲洗：否; 商品编号：3205548",
    },
    "3217087": {
        "name": "保暖内衣", "cat1": "运动用品", "spec": "尺码S-XL",
        "description": "保暖舒适\n超级柔软\n采用95%再生聚酯纤维制成\n寒冷天气穿上这款保暖内衣，保持温暖。适合作为运动、散步或寒冷环境工作时的基础层。",
        "details": "服装尺码：XL; 颜色：白色; 材质：聚酯纤维、弹性纤维; 性别：女; 洗涤说明：最高40°C机洗; 熨烫说明：不可熨烫; 烘干说明：不可滚筒烘干; 保暖功能：是; 商品编号：3217087",
    },
    "3206733": {
        "name": "圣诞灯光音乐装饰", "cat1": "家居布置", "spec": "多种款式",
    },
    "3218567": {
        "name": "金属探测器", "cat1": "DIY五金", "cat2": "工具",
        "description": "使用2节1.5V AAA电池（不含）\n高度可调\n你是真正的寻宝高手吗？带上这款出色的金属探测器去探索，也许就能发现真正的宝藏！",
        "details": "适用年龄：5岁以上; 商品编号：3218567",
    },
    "3208477": {
        "name": "丙烯颜料喷雾", "cat1": "兴趣手作", "cat2": "颜料", "spec": "6×50ml｜多种颜色",
        "description": "6种不同颜色\n每瓶50ml的小巧瓶装\n适用于布料、纸张、纸板和木材\n用这套丙烯喷漆尽情释放创造力！6瓶小巧装颜料拥有柔和色调，非常适合制作卡片、绘画和其他手工项目。实用喷头可快速均匀地上色，适合初学者和有经验的手工爱好者。",
        "details": "颜色：多色; 数量：6瓶; 含量：300ml; 艺术家颜料/着色剂类型：艺术家丙烯颜料; 商品编号：3208477",
    },
    "3223396": {
        "name": "冬阴功味米粉", "cat1": "食品饮料", "cat2": "食品", "spec": "78g",
        "description": "美味冬阴功风味\n用沸水即可轻松制作\n米粉\n这款冬阴功味米粉可快速端上餐桌。加入沸水，稍等片刻即可享用。快捷、美味又简单，非常适合工作时或露营时食用。",
        "details": "添加香料/配料：是; 制作方式：油炸; 面食/米粉类型：米粉; 商品编号：3223396",
    },
    "3219651": {
        "name": "Vision热敏打印纸", "cat1": "办公文具", "cat2": "纸品", "spec": "5卷｜多种款式",
        "description": "适用于儿童相机、拍立得相机或热敏打印机\n每卷5米\n可打印约270张",
        "details": "颜色：多色、白色; 数量：5卷; 商品编号：3219651",
    },
    "3221995": {
        "name": "Spidey游戏套装", "cat1": "玩具", "spec": "多种款式",
    },
    "3009463": {
        "name": "假指甲", "cat1": "个人美容", "spec": "24个｜多种款式",
        "description": "含指甲胶\n提供多种颜色或图案",
        "details": "颜色：蓝色、绿色、多色、图案、粉色; 材质：塑料; 数量：24个; 含指甲胶：是; 性别：女; 假指甲类型：假指甲; 商品编号：3009463",
    },
}


FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")


def main() -> None:
    cfg = load_settings()
    db_path = database_path(cfg)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = Path(cfg["paths"]["backups"]) / f"action_tracker_before_model_translation_remaining_{stamp}.db"
    backup_database(db_path, backup)
    import_id = f"model-translation-remaining-{stamp}"
    now = datetime.now(timezone.utc).isoformat()
    changed_skus = 0
    changed_fields = 0
    source_missing = []

    with connect(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        for sku, translated in TRANSLATIONS.items():
            es = db.execute(
                "SELECT source_hash,name,cat1,cat2,spec,description,details FROM product_localizations "
                "WHERE official_sku=? AND language='es'", (sku,)
            ).fetchone()
            zh = db.execute(
                "SELECT name,cat1,cat2,spec,description,details FROM product_localizations "
                "WHERE official_sku=? AND language='zh'", (sku,)
            ).fetchone()
            if not es or not zh:
                raise RuntimeError(f"LOCALIZATION_ROW_MISSING:{sku}")
            source_hash = str(es[0] or "")
            old = dict(zip(FIELDS, zh))
            merged = dict(old)
            for field, value in translated.items():
                if field not in FIELDS:
                    raise RuntimeError(f"UNKNOWN_FIELD:{sku}:{field}")
                merged[field] = value

            changed = [field for field in FIELDS if old[field] != merged[field]]
            if changed:
                changed_skus += 1
                changed_fields += len(changed)
            field_source = {field: "MODEL_GPT_5.6_LUNA" for field in changed}
            # A genuinely absent official source field remains absent and is
            # explicitly marked as such rather than being model-invented.
            for field in FIELDS:
                if es[FIELDS.index(field) + 1] in (None, "") and field not in translated:
                    field_source[field] = "OFFICIAL_SOURCE_MISSING"
                    source_missing.append((sku, field))

            missing_for_sku = [field for item_sku, field in source_missing if item_sku == sku]
            row_status = "MODEL_TRANSLATED_PARTIAL" if missing_for_sku else "MODEL_TRANSLATED"
            db.execute(
                """UPDATE product_localizations SET name=?,cat1=?,cat2=?,spec=?,description=?,details=?,
                source=?,review_status=?,resolution_status=?,name_source=?,cat1_source=?,cat2_source=?,
                spec_source=?,description_source=?,details_source=?,freshness_status=?,approved_by=?,approved_at=?,
                applied_commit_id=?,updated_at=? WHERE official_sku=? AND language='zh'""",
                (merged["name"], merged["cat1"], merged["cat2"], merged["spec"], merged["description"], merged["details"],
                 "MODEL_GPT_5.6_LUNA", row_status, row_status,
                 field_source.get("name", "MODEL_GPT_5.6_LUNA"),
                 field_source.get("cat1", "MODEL_GPT_5.6_LUNA"),
                 field_source.get("cat2", "MODEL_GPT_5.6_LUNA"),
                 field_source.get("spec", "MODEL_GPT_5.6_LUNA"),
                 field_source.get("description", "MODEL_GPT_5.6_LUNA"),
                 field_source.get("details", "MODEL_GPT_5.6_LUNA"),
                 "CURRENT", "user-requested-model-translation", now, import_id, now, sku),
            )
            db.execute("UPDATE products SET name_zh=?,updated_at=? WHERE official_sku=?", (merged["name"], now, sku))
            sync_row = {"sku": sku, "language": "zh", **merged, "source": "MODEL_GPT_5.6_LUNA",
                        "review_status": row_status, "source_hash": source_hash,
                        "applied_commit_id": import_id, "freshness_status": "CURRENT"}
            for field in FIELDS:
                sync_row[f"{field}_source"] = field_source.get(field, "MODEL_GPT_5.6_LUNA")
                sync_row[f"{field}_review_status"] = "APPROVED" if field in translated else "SOURCE_MISSING"
            sync_localization_field_provenance(db, sync_row, commit_id=import_id, now=now)
            for field in changed:
                patch_id = create_patch(
                    db, official_sku=sku, language="zh", field_name=field,
                    old_value=old[field], new_value=merged[field], source_hash=source_hash,
                    reason="user_requested_model_translation", created_by="GPT-5.6-LUNA",
                )
                append_patch_event(db, patch_id, "PATCH_APPROVED", actor="user-requested-model-translation", reason="用户授权模型翻译")
                append_patch_event(db, patch_id, "PATCH_APPLIED", actor="GPT-5.6-LUNA", reason="中文字段写入 SQLite PRIMARY")

            # Close the broad request.  Missing official source fields are not
            # re-queued as translation work because there is nothing to translate.
            db.execute(
                "UPDATE translation_queue SET status='COMPLETED',completed_at=?,last_error=? "
                "WHERE official_sku=? AND language='zh' AND status!='COMPLETED'",
                (now, "SOURCE_FIELD_MISSING:" + ",".join(f for s, f in source_missing if s == sku) if any(s == sku for s, _ in source_missing) else None, sku),
            )
        db.commit()

    head = __import__("action_tracker.database.repository", fromlist=["ProductionRepository"]).ProductionRepository(db_path).current_head()
    sync = regenerate_compatibility_exports(cfg, head) if head else None
    report = {
        "import_id": import_id,
        "backup": str(backup),
        "changed_skus": changed_skus,
        "changed_fields": changed_fields,
        "source_missing": [{"sku": sku, "field": field} for sku, field in source_missing],
        "model": "GPT-5.6-LUNA",
        "master_sync": sync,
        "finished_at": now,
    }
    output = Path(cfg["paths"]["logs"]) / f"model_translation_remaining_{stamp}.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({**report, "report": str(output)}, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
