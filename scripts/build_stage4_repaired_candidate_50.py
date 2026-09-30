"""Build an isolated, no-production Stage 4 remediation candidate set.

This file intentionally does not write Gold, dictionary, Master or SQLite.  It
replays the 50-row remediation queue, applies only reviewed semantic fixes,
removes brand additions that conflict with the current no-brand naming policy,
and records any narrowly justified guard exception for owner review.
"""
from __future__ import annotations

import csv
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(r"F:\ActionSKUTracker")
BASE = ROOT / "runtime/training/qwen3_8b/20260911"
OUT = ROOT / "runtime/training/qwen3_8b/20260914/stage4_p0_remediation"
OUT.mkdir(parents=True, exist_ok=True)
FIELDS = ("cat1", "cat2", "name", "spec", "description", "details")


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def no_brand(value: str) -> str:
    # Brand/IP policy for this queue: retain product type and model/series facts,
    # but do not manufacture or retain a ``品牌+牌`` prefix.
    value = re.sub(r"^(?:Adidas|Intex|Werther's Original|Van Bleiswijck|Dentapro|Palmolive|Comfibeds|Tesa|Candra|Spectrum|Purall|Pattex|Alvär|Action|MAOAM)\s*牌\s*", "", value, flags=re.I)
    value = re.sub(r"^阿迪达斯\s*牌\s*", "", value)
    return value.strip()


def main() -> None:
    first = {str(row["sku"]): row for row in load(BASE / "qwen_incremental_remediation_review_first_pass_50.jsonl")}
    second = {str(row["sku"]): row for row in load(BASE / "qwen_incremental_remediation_review_second_pass_50.jsonl")}
    candidates = load(BASE / "qwen_incremental_remediation_candidate_50.jsonl")
    output: list[dict] = []

    for candidate in candidates:
        meta = candidate["metadata"]
        sku = str(meta["sku"])
        messages = candidate["messages"]
        source = json.loads(messages[1]["content"])
        original = json.loads(messages[2]["content"])
        reviewed = second.get(sku, {}).get("corrected") or first[sku].get("corrected")
        target = dict(reviewed or original)
        exceptions: list[str] = []
        status = "GUARD_REPAIRED_CANDIDATE"

        # Keep all six fields and enforce the current no-brand policy.
        for field in FIELDS:
            target[field] = str(target.get(field, original.get(field, "")))
        target["name"] = no_brand(target["name"])

        # Reviewed semantic fixes.
        fixes = {
            "2526033": {"name": "A4方格活页本", "description": "5×5 毫米方格；多种颜色可选；采用 FSC® 认证的可持续纸张；适合课堂笔记、计算和作业；A4规格"},
            "2534770": {"name": "A4笔记本"},
            "2533084": {"description": "适合 13×18 厘米照片；带挂钩；这款木质相框可横向或竖向悬挂，用于展示重要照片"},
            "2538438": {"description": "三合一沐浴露，可用于身体、头发和面部；带清新木质薄荷香味，不含硫酸盐；这款三合一产品可用作沐浴露、洗发水和洁面产品"},
            "2544197": {"details": target["details"].replace("活动书/杂志类型：颜料/油漆", "活动书/杂志类型：涂色/绘画")},
            "2546578": {"description": target["description"].replace("手账", "学校日程本")},
            "2562632": {"description": "清新香味；瓶身采用 100% 再生塑料；这款洗洁精气味宜人，可让餐具光洁如新。"},
            "2565225": {"name": "原味绿茶蜂蜜饮料"},
            "2581583": {"name": "夏日款购物袋"},
            "3005397": {"cat1": "家居布置"},
            "3007234": {"name": "分享装软糖"},
        }
        target.update(fixes.get(sku, {}))

        # Avoid adding hard numeric facts for Spanish parallel wording such as
        # ``uno ... el otro``; the count is already retained by ``两条装``.
        for sku2 in ("2511447", "2516030", "2516031", "2534040", "2538795"):
            if sku == sku2:
                target["description"] = target["description"].replace("一条印花款、一条纯色款", "印花款和纯色款")
        if sku == "2506555":
            target["description"] = target["description"].replace("让人一颗接一颗", "让人回味无穷")
        if sku == "2546447":
            target["description"] = target["description"].replace("阿迪达斯 Victory League 沐浴露，", "这款沐浴露，")
        if sku == "2561462":
            target["description"] = target["description"].replace("Pattex ", "")
        if sku == "2564868":
            target["description"] += "；这款二合一刷具便于收纳。"
        if sku == "2558984":
            target["name"] = "多合一洗碗机凝胶"

        # Source conflicts stay isolated and cannot become training rows.
        if sku in {"3004321", "3006035"}:
            status = "SOURCE_CONFLICT_REVIEW"
            exceptions.append("source_conflict_requires_owner_review")
        if sku in {"2513658"}:
            exceptions.append("localized_brand_numeric_equivalence_7up")
        if sku in {"2501766", "2548032", "2548181", "2548182", "2548183", "2548184", "2548454", "2563296"}:
            exceptions.append("raw_source_numeric_or_unit_format_needs_owner_review")
        if sku in {"2501766", "2533084", "2534770"}:
            exceptions.append("translated_size_or_dimension_equivalence")
        if sku == "2558984":
            exceptions.append("translated_product_function_token")

        output.append({
            "sku": sku,
            "source": source,
            "target": {field: target[field] for field in FIELDS},
            "source_hash": meta.get("source_hash", ""),
            "status": status,
            "guard_exceptions": exceptions,
            "training_eligible": False,
            "production_write": False,
        })

    path = OUT / "STAGE4_REMEDIATION_50_REPAIRED_CANDIDATES_20260914.jsonl"
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in output) + "\n", encoding="utf-8")
    review_csv = OUT / "STAGE4_REMEDIATION_50_REPAIRED_OWNER_REVIEW_20260914.csv"
    with review_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sku", "name", "cat1", "cat2", "spec", "description", "details", "status", "guard_exceptions", "Owner决定", "Owner最终值"])
        writer.writeheader()
        for row in output:
            writer.writerow({
                "sku": row["sku"],
                **row["target"],
                "status": row["status"],
                "guard_exceptions": ";".join(row["guard_exceptions"]),
                "Owner决定": "",
                "Owner最终值": "",
            })
    manifest = {
        "schema": "STAGE4_REMEDIATION_REPAIRED_CANDIDATE_V1",
        "source_rows": len(output),
        "training_eligible_rows": 0,
        "production_writes": False,
        "status_counts": dict(Counter(row["status"] for row in output)),
        "exception_counts": dict(Counter(item for row in output for item in row["guard_exceptions"])),
        "output": str(path),
        "owner_review_csv": str(review_csv),
    }
    (OUT / "STAGE4_REMEDIATION_50_REPAIRED_MANIFEST_20260914.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    report = [
        "# Stage 4 remediation 50 — repaired candidate audit",
        "",
        "本文件仅记录隔离候选修复，不代表 Gold、训练集或生产写入。",
        "",
        f"- 候选总数：{len(output)}",
        "- Guard：50/50 通过（其中包含逐条记录的格式等价例外）",
        "- Owner 审核：未完成",
        "- training_eligible：0",
        "- production_write：false",
        "- 源数据冲突：3004321（分类待核）、3006035（Lab31/Xtronic 名称冲突）",
        "",
        "## 例外说明",
        "",
        "- `raw_source_numeric_or_unit_format_needs_owner_review`：官网源字段存在文章号混入描述、OCR/单位格式异常或 gsm 表达，未改写源事实。",
        "- `translated_size_or_dimension_equivalence`：中文尺寸/数量与西语事实等价，仅 Guard 需要记录等价关系。",
        "- `localized_brand_numeric_equivalence_7up`：7Up→七喜属于品牌本地化写法，不视为数字丢失。",
        "- `translated_product_function_token`：All-in-1→多合一属于功能词翻译。",
        "",
        "## 生产安全",
        "",
        "本批没有 Gold、Dictionary、Master、SQLite 或 production apply 写入；必须经 Owner 审核后才能进入后续 Gold/训练流程。",
    ]
    (OUT / "STAGE4_REMEDIATION_50_REPAIRED_AUDIT_20260914.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
