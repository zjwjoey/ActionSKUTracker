"""Build an isolated Translation Memory V1 staging package.

Only Phase-A ``TM_READY_CANDIDATE`` rows are staged.  The package is explicitly
pending Owner approval and is not a production dictionary import.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    queue = [json.loads(line) for line in args.queue.read_text(encoding="utf-8").splitlines() if line.strip()]
    ready = [row for row in queue if row.get("candidate_status") == "TM_READY_CANDIDATE"]
    review = [row for row in queue if row.get("candidate_status") != "TM_READY_CANDIDATE"]
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)

    tm_rows: list[dict[str, Any]] = []
    for row in ready:
        tm_rows.append({
            "tm_id": hashlib.sha256(f"tm-v1|{row['pair_hash']}".encode("utf-8")).hexdigest(),
            "source_language": "es",
            "target_language": "zh-CN",
            "field_name": row["field"],
            "source_value_raw": row["source_value_raw"],
            "source_value_normalized": row["source_value_raw"].strip(),
            "target_value": row["target_value"],
            "source_hash": row.get("source_hash", ""),
            "pair_hash": row["pair_hash"],
            "sku_example": row.get("sku", ""),
            "review_status": "STAGED_PENDING_OWNER_APPROVAL",
            "policy_version": "NO_BRAND_IP_DEFAULT_V1",
            "provenance": row.get("provenance", []),
        })
    tm_rows.sort(key=lambda row: (row["field_name"], row["source_value_normalized"], row["tm_id"]))
    tm_path = out / "translation_memory_v1_staging.jsonl"
    tm_path.write_text("".join(canonical(row) + "\n" for row in tm_rows), encoding="utf-8")

    columns = ["tm_id", "field_name", "source_value_raw", "target_value", "source_hash", "pair_hash", "sku_example", "review_status", "policy_version"]
    with (out / "translation_memory_v1_staging.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows({column: row.get(column, "") for column in columns} for row in tm_rows)

    review_columns = ["candidate_status", "field", "source_value_raw", "target_value", "source_hash", "pair_hash", "sku", "gold_status", "guard_status", "policy_flags", "occurrence_count"]
    with (out / "tm_owner_review_queue.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=review_columns)
        writer.writeheader()
        for row in review:
            writer.writerow({
                column: "; ".join(row.get(column, [])) if isinstance(row.get(column), list) else row.get(column, "")
                for column in review_columns
            })

    field_counts = Counter(row["field_name"] for row in tm_rows)
    manifest = {
        "artifact_type": "ACTION_TMS_TRANSLATION_MEMORY_V1_STAGING",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_queue": str(args.queue.resolve()),
        "source_queue_sha256": sha256_file(args.queue),
        "staged_tm_rows": len(tm_rows),
        "owner_review_rows": len(review),
        "staged_tm_by_field": dict(sorted(field_counts.items())),
        "review_queue_by_status": dict(Counter(row.get("candidate_status", "") for row in review)),
        "review_status": "STAGED_PENDING_OWNER_APPROVAL",
        "production_writes": False,
        "dictionary_writes": False,
        "sqlite_writes": False,
        "master_writes": False,
        "fuzzy_match_auto_apply": False,
        "next_gate": "Owner approval required before TM/Termbase import; this staging package is not production data.",
        "outputs": {
            "tm_jsonl": str(tm_path),
            "tm_csv": str((out / "translation_memory_v1_staging.csv").resolve()),
            "owner_review_queue": str((out / "tm_owner_review_queue.csv").resolve()),
        },
    }
    (out / "tm_v1_staging_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = [
        "# Translation Memory V1 Staging",
        "",
        "本包仅为 Owner 审核前的独立 staging，不是生产字典，不会自动 Apply。",
        "",
        f"- TM 候选：{len(tm_rows)} 条",
        f"- Owner 复核队列：{len(review)} 条",
        f"- 字段分布：{dict(sorted(field_counts.items()))}",
        "",
        "## 门禁",
        "",
        "- 精确 source pair 才能自动候选；模糊匹配不自动采用。",
        "- 品名品牌/IP策略复核和多个批准译文冲突均未进入 staging TM。",
        "- 未执行 Master、Dictionary、SQLite 或生产配置写入。",
        "",
        "## 文件",
        "",
        f"- `{tm_path}`",
        f"- `{out / 'translation_memory_v1_staging.csv'}`",
        f"- `{out / 'tm_owner_review_queue.csv'}`",
        f"- `{out / 'tm_v1_staging_manifest.json'}`",
    ]
    (out / "TM_V1_STAGING_REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps({"status": "TM_V1_STAGING_READY_OWNER_REVIEW", "staged_tm_rows": len(tm_rows), "owner_review_rows": len(review), "output_dir": str(out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
