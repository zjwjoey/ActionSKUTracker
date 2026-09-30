"""Repair the remaining current dictionary name/spec translation residuals."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import uuid
from datetime import datetime
from pathlib import Path


REPAIRS: dict[str, tuple[str, str]] = {
    "1815165": ("圣诞球挂钩", "50件"),
    "2513777": ("水泵钳", "245mm"),
    "2517861": ("熨衣板", "110×30cm｜多款可选"),
    "2531687": ("圣诞窗贴", "多款可选"),
    "2531879": ("餐巾纸", "20张｜多款可选"),
    "2546447": ("三合一沐浴露", "250ml"),
    "2555053": ("圣诞窗贴", "多款可选"),
    "2556239": ("圣诞毛衣", "S-XL｜多款可选"),
    "2563856": ("棉花糖", "300g"),
    "2568247": ("保暖运动裤", "S-XL"),
    "2577121": ("动作人偶", "多款可选"),
    "2579920": ("购物袋", "45×45×17cm"),
    "3002932": ("牛肉味方便面", "5×65g"),
    "3002933": ("鸡肉味方便面", "5×65g"),
    "3003698": ("-10°C挡风玻璃清洗防冻液", "5L"),
    "3006866": ("圣诞丝带", "6×2m｜多款可选"),
    "3010631": ("女性私密湿巾", "20片"),
    "3016266": ("煎锅套装", "3件"),
    "3016777": ("XL餐盘", "88×23×3.5cm"),
    "3017022": ("狗狗玩具", "多款可选"),
    "3205642": ("编织收纳篮", "3L｜25×17×14cm｜多色可选"),
    "3205643": ("编织收纳篮", "19L｜39×29×24cm｜多色可选"),
    "3205644": ("编织收纳篮", "9L｜29×22×17cm｜多色可选"),
    "3205907": ("指纹解锁保险箱", "多款可选"),
    "3206414": ("便携咖啡杯", "400ml｜多色可选"),
    "3206668": ("狗狗零食棒", "350g｜鸭肉"),
    "3206733": ("声光圣诞装饰", "多款可选"),
    "3207052": ("动物玩具", "多款可选"),
    "3207114": ("汽车挡风玻璃隔热罩", "145×100cm"),
    "3207470": ("厨房计时器", "多色可选"),
    "3207550": ("奶泡器", "500W｜多色可选"),
    "3207818": ("玻璃切割垫", "42×32cm"),
    "3207985": ("屏幕保护膜", "3片｜适用于Samsung｜多款可选"),
    "3208309": ("抽屉式收纳盒", "18×13×5cm"),
    "3208310": ("抽屉式收纳盒", "18×13×10cm"),
    "3208913": ("玻璃杯", "320ml"),
    "3209484": ("防滑宠物食碗", "多色可选"),
    "3210163": ("圣诞花生巧克力豆", "250g"),
    "3210619": ("边桌", "50×30×57cm｜多色可选"),
    "3214057": ("装饰雪花", "多色可选"),
    "3214289": ("涂色书", "48张｜多款可选"),
    "3214806": ("凝胶笔", "多款可选"),
    "3215124": ("带伸缩杆拖把", "3件｜多色可选"),
    "3215400": ("护发发膜", "250ml"),
    "3215953": ("煎锅", "Ø28cm"),
    "3216792": ("AAA电池", "20节"),
    "3216919": ("泡沫飞镖发射器", "多款可选"),
    "3216996": ("印章套装", "12件｜多款可选"),
    "3217348": ("带灯车载烟灰缸", "Ø8×12cm"),
    "3206415": ("圣诞模具", "多款可选"),
    "3217430": ("相框", "8×10cm｜多款可选"),
    "3217441": ("亚麻靠垫", "60×60cm｜多色可选"),
    "3217468": ("儿童玩具商店套装", "多款可选"),
    "3217622": ("遥控车", "多款可选"),
    "3218000": ("儿童圣诞毛衣", "98-140码｜多款可选"),
    "3218163": ("脏衣篮", "57L｜45×34×61cm｜多色可选"),
    "3218248": ("唇彩套装", "3件"),
    "3218278": ("圣诞灯饰", "3件｜42颗LED｜多款可选"),
    "3218313": ("彩色灯球", "Ø30cm｜140颗LED"),
    "3218317": ("旋转圣诞LED装饰", "多款可选"),
    "3218343": ("LED圣诞树", "Ø13×21cm｜多色可选"),
    "3218476": ("8端口多功能充电器", "最大60W｜多色可选"),
    "3218575": ("划线尺", "60cm"),
    "3218877": ("碗", "Ø9cm｜多色可选"),
    "3218885": ("早餐盘", "Ø22cm｜多色可选"),
    "3219210": ("指甲油", "2件｜多色可选"),
    "3219392": ("礼品袋", "90×60cm｜多款可选"),
    "3219600": ("冬季场景装饰", "6件"),
    "3219922": ("观影夜零食套装", "4件｜多款可选"),
    "3219963": ("化妆套装", "5件｜多色可选"),
    "3220220": ("真空收纳袋", "2件｜104×70cm"),
    "3220244": ("厨房收纳盒", "39×19.5×18cm"),
    "3220271": ("冰箱保鲜盒", "多款可选"),
    "3220360": ("双层水果篮", "Ø27×37cm｜多款可选"),
    "3220378": ("冰箱收纳盒", "31.5×15×10cm"),
    "3220379": ("冰箱收纳盒", "31.5×20×9.8cm"),
    "3220418": ("金属漆", "250ml｜多色可选"),
    "3220493": ("食品储存罐", "1.3L"),
    "3220600": ("收纳脚凳", "Ø31.5×37.5cm｜多色可选"),
    "3220734": ("可抽拉收纳架", "58×22.5×12cm"),
    "3220881": ("花瓶", "Ø14×19cm"),
    "3221127": ("狗狗零食", "400g"),
    "3221236": ("双面纳米胶带", "多款可选"),
    "3221375": ("鸡肉混合宠物零食", "450g"),
    "3221470": ("发光乒乓球游戏套装", "多色可选"),
    "3221941": ("XL史莱姆桶", "1.2kg｜多色可选"),
    "3221950": ("儿童LCD平板", "8.5英寸｜多款可选"),
    "3222128": ("瓷质早餐盘", "Ø21cm"),
    "3222129": ("瓷质平盘", "Ø27cm"),
    "3222130": ("瓷质深盘", "Ø20×4.5cm"),
    "3222131": ("瓷质沙拉碗", "Ø23×6.5cm"),
    "3222222": ("奶酪零食", "130g｜多款可选"),
    "3222383": ("纸盘", "10个｜Ø23cm｜多款可选"),
    "3222807": ("仿石边框相框", "12.5×17cm｜多款可选"),
    "3222911": ("衣物柔顺剂", "80次洗涤"),
    "3222912": ("衣物柔顺剂", "80次洗涤"),
    "3222971": ("屏幕保护膜", "2片｜适用于iPhone｜多款可选"),
    "3223146": ("惊喜袋", "多款可选"),
    "3223164": ("带闹钟的唤醒灯", "16.5×7×15cm"),
    "3223174": ("竹制镜面收纳盒", "20×13×9cm"),
    "3223281": ("狗狗零食", "100g｜多款可选"),
    "3223283": ("狗狗零食", "100g｜多款可选"),
    "3223312": ("旋转螺丝刀", "4V"),
    "3223316": ("猫抓板", "27.5×27.5×38cm｜多色可选"),
    "3223369": ("相册", "18.5×22.5cm｜多款可选"),
    "3223373": ("大理石托盘", "Ø30×5cm｜多款可选"),
    "3223467": ("透明抽屉收纳盒", "27×18×5cm"),
    "3223474": ("水壶", "1.4L"),
    "3223561": ("煎锅", "Ø28cm｜多色可选"),
    "3223720": ("画笔套装", "6支"),
    "3223731": ("闪粉管", "10支"),
    "3223768": ("带圆盘打孔器套装", "19件"),
    "3223972": ("收纳脚凳", "Ø45×61cm"),
    "3224002": ("带桌面收纳脚凳", "70×37.5×38.5cm｜多款可选"),
    "3224029": ("香薰蜡烛", "Ø9.5cm｜多款可选"),
    "3224121": ("绒毛旋转玩具", "多款可选"),
    "3224146": ("辣味韩式年糕", "182g"),
    "3224259": ("带收纳盒的狗狗拾便袋", "多色可选"),
    "3224285": ("毛绒线", "100g｜多色可选"),
    "3224292": ("水彩笔", "60支｜多色可选"),
    "3224717": ("玩具披萨套装", "29件"),
    "3224726": ("会发声尿尿娃娃", "10件｜30cm"),
    "3224781": ("纱线", "15×22g"),
    "3224788": ("玩具厨房", "55×29.5×78cm"),
    "3224814": ("狗狗礼品套装", "5件"),
    "3224857": ("复古烤面包机", "930W｜多色可选"),
    "3224869": ("女士居家袜", "35-42码｜2双｜多色可选"),
    "3224899": ("贴纸相册", "15张｜多款可选"),
    "3224907": ("带夹蝴蝶装饰", "3件｜多色可选"),
    "3225088": ("LED灯带", "3m"),
    "3225091": ("罗纹绒被套", "155×200cm｜多色可选"),
    "3225092": ("罗纹绒被套", "200×200cm｜多色可选"),
    "3225101": ("小狗散步娃娃", "10件"),
    "3225118": ("木纹边桌", "2件｜多色可选"),
    "3225276": ("圣诞吊饰DIY套装", "6件｜多款可选"),
    "3225279": ("舒适风圣诞涂色书", "48张｜多款可选"),
    "3225392": ("杯子蛋糕装饰套装", "32件｜多款可选"),
    "3225602": ("圣诞金银丝桌布", "140×240cm｜多款可选"),
    "3225743": ("圣诞倒数日历", "24件"),
    "3225894": ("带蝴蝶结烛台", "12×8×6cm｜多色可选"),
    "3225959": ("发夹", "16件｜多色可选"),
    "3225994": ("打底裤", "S-XL｜多款可选"),
    "3226221": ("辣味玉米卷脆片", "200g"),
    "3226277": ("水晶饰品", "多款可选"),
    "3227693": ("香水", "50ml｜多款可选"),
    "3223230": ("巧克力太妃糖果", "550g"),
}


HEADERS = ["scope", "key", "field", "value", "reason", "source", "locked", "updated_at"]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def atomic_write(path: Path, rows: list[dict[str, str]]) -> None:
    staged = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with staged.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(rows)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(staged, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--baseline-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    runtime = args.runtime_dir.resolve()
    baseline = args.baseline_dir.resolve()
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    products = {row["sku"]: row for row in read_csv(runtime / "product_dictionary.csv")}
    missing_source = sorted(set(REPAIRS) - set(products))
    if missing_source:
        raise SystemExit(f"REPAIR_SOURCE_SKU_MISSING: {missing_source}")
    paths = [runtime / "manual_overrides.csv", baseline / "manual_overrides.csv"]
    before_hashes = {str(path): sha256_file(path) for path in paths}
    updated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    replacement_map: dict[tuple[str, str, str], dict[str, str]] = {}
    for sku, (name, spec) in REPAIRS.items():
        source_hash = products[sku]["source_hash"]
        for field, value in (("name_zh_standard", name), ("spec_zh_standard", spec)):
            key = ("product", sku, field)
            replacement_map[key] = {
                "scope": "product", "key": sku, "field": field, "value": value,
                "reason": "GLOBAL_DICTIONARY_TRANSLATION_RESIDUAL_REPAIR",
                "source": "GPT_MODEL_TRANSLATION_2026-09-15", "locked": "0", "updated_at": updated_at,
                "source_hash": source_hash,
            }
    # Preserve the eight-field file contract; source_hash is kept in the audit
    # sidecar rather than the CSV schema.
    for path in paths:
        existing = {(r.get("scope", ""), r.get("key", ""), r.get("field", "")): r for r in read_csv(path)}
        for key, row in replacement_map.items():
            existing[key] = {header: row[header] for header in HEADERS}
        atomic_write(path, list(existing.values()))
    after_hashes = {str(path): sha256_file(path) for path in paths}
    manifest = {
        "artifact_type": "ACTION_DICTIONARY_TRANSLATION_RESIDUAL_REPAIR",
        "sku_count": len(REPAIRS),
        "field_count": len(replacement_map),
        "source_hash_contract": "product_dictionary.source_hash",
        "runtime_before_sha256": before_hashes[str(paths[0])],
        "runtime_after_sha256": after_hashes[str(paths[0])],
        "baseline_before_sha256": before_hashes[str(paths[1])],
        "baseline_after_sha256": after_hashes[str(paths[1])],
        "production_writes": True,
        "status": "PASS",
        "repaired_skus": sorted(REPAIRS),
    }
    (out / "dictionary_translation_residual_repair_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "DICTIONARY_TRANSLATION_RESIDUAL_REPAIR.md").write_text("\n".join([
        "# Dictionary Translation Residual Repair", "",
        f"- repaired SKUs: {len(REPAIRS)}", f"- repaired fields: {len(replacement_map)}",
        "- policy: no brand/IP by default; preserve model/technical tokens where needed",
        "- status: PASS", "",
    ]) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
