"""Write an auditable report for the grouped, production-like Stage 5 gate."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", type=Path, required=True)
    ap.add_argument("--candidates", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    args = ap.parse_args()
    fixture_manifest = json.loads(args.fixture.with_suffix(".manifest.json").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in args.candidates.read_text(encoding="utf-8").splitlines() if line.strip()]
    review = [row for row in rows if row.get("status") == "REVIEW_REQUIRED"]
    accepted = [row for row in rows if row.get("accepted_by_guard")]
    reasons = Counter(reason for row in review for reason in (row.get("reasons") or []))
    payload = {
        "artifact_type": "STAGE5_REALISTIC_FULL_RECORD_GATE_V1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fixture": str(args.fixture.resolve()),
        "fixture_sha256": sha256(args.fixture),
        "fixture_manifest": fixture_manifest,
        "candidates": str(args.candidates.resolve()),
        "candidates_sha256": sha256(args.candidates),
        "rows": len(rows),
        "unique_sku_count": len({str(row.get("sku")) for row in rows}),
        "accepted_by_guard": len(accepted),
        "review_required": len(review),
        "reason_counts": dict(sorted(reasons.items())),
        "review_skus": [str(row.get("sku")) for row in review],
        "production_writes": False,
        "master_written": False,
        "dictionary_written": False,
        "sqlite_written": False,
        "release_status": "BLOCKED_REVIEW_REQUIRED" if review else "PASS",
        "review_records": review,
    }
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "stage5_realistic_full_record_gate.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# Stage 5 realistic full-record gate",
        "",
        "本报告使用完整六字段 source-only 记录，先走字典闭环，再只对 resolver gap 调用适配器；不执行任何生产写入。",
        "",
        f"- 完整记录：`{len(rows)}`（字段测试集另有 `{fixture_manifest.get('incomplete_group_count', 0)}` 条源字段不完整，已排除）",
        f"- Guard 自动通过：`{len(accepted)}`",
        f"- 需要审核：`{len(review)}`",
        f"- 原因统计：`{dict(sorted(reasons.items()))}`",
        f"- 结论：`{payload['release_status']}`",
        "",
        "## 需要审核的记录",
        "",
        "| SKU | 原因 | 模型缺口字段 |",
        "|---|---|---|",
    ]
    for row in review:
        lines.append(f"| {row.get('sku')} | {', '.join(row.get('reasons') or [])} | {', '.join(row.get('model_gap_fields') or [])} |")
    (out / "STAGE5_REALISTIC_FULL_RECORD_GATE.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(json_path), "rows": len(rows), "accepted": len(accepted), "review": len(review), "release_status": payload["release_status"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
