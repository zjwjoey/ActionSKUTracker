"""Build the full manual source-fidelity review queue for a benchmark."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from compare_qwen_baselines import load_rows, source_target


def build_queue(test_file: Path, benchmark_file: Path, output_file: Path) -> dict[str, Any]:
    report = json.loads(benchmark_file.read_text(encoding="utf-8"))
    current = next(
        model for model in report.get("models", [])
        if model.get("name") == "current_combined_adapter"
    )
    issue_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for issue in current.get("issues", []):
        key = (str(issue.get("sku", "")), str(issue.get("field", "")))
        issue_by_key.setdefault(key, []).append(issue)

    records = []
    for row in load_rows(test_file):
        source, expected = source_target(row)
        sku = str(row.get("metadata", {}).get("sku", ""))
        records.append({
            "review_id": f"QWEN3_ACTION_20260911_V1:{sku}",
            "sku": sku,
            "status": "PENDING_MANUAL_REVIEW",
            "source_es": source,
            "reference_zh": expected,
            "automated_issue_count": sum(len(issue_by_key.get((sku, field), [])) for field in source),
            "automated_issues": [
                issue
                for field in source
                for issue in issue_by_key.get((sku, field), [])
            ],
            "review_instruction": "逐字段对照 source_es，确认 reference/model 输出是否忠实；任何数字、单位、否定、品牌或跨字段事实错误均阻断 Stage 5。",
        })
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    summary = {
        "queue_file": str(output_file.resolve()),
        "rows": len(records),
        "pending_manual_review": len(records),
        "automated_issue_rows": sum(bool(record["automated_issues"]) for record in records),
        "automated_issue_count": sum(record["automated_issue_count"] for record in records),
        "status": "PENDING_MANUAL_REVIEW",
    }
    output_file.with_suffix(".summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-file", type=Path, required=True)
    parser.add_argument("--benchmark-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_queue(args.test_file, args.benchmark_file, args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
