"""Convert benchmark Guard reasons into an auditable error taxonomy report."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REASON_TO_TAXONOMY = {
    "NUMERIC_DROPPED": "NUMERIC_MISSING",
    "NUMERIC_HALLUCINATED": "NUMERIC_ADDED",
    "INVALID_CAT1": "CATEGORY_INVALID",
    "SPANISH_RESIDUAL": "SPANISH_RESIDUAL",
    "ENGLISH_RESIDUAL": "ENGLISH_RESIDUAL",
}


def build_report(benchmark_file: Path, output_file: Path, model_name: str = "current_combined_adapter") -> dict[str, Any]:
    report = json.loads(benchmark_file.read_text(encoding="utf-8"))
    model = next(item for item in report.get("models", []) if item.get("name") == model_name)
    records = []
    counts: Counter[str] = Counter()
    for issue in model.get("issues", []):
        reasons = [str(reason) for reason in issue.get("reasons", [])]
        taxonomy = sorted({REASON_TO_TAXONOMY.get(reason, "UNCLASSIFIED") for reason in reasons})
        counts.update(taxonomy)
        records.append({
            "sku": str(issue.get("sku", "")),
            "field": str(issue.get("field", "")),
            "guard_reasons": reasons,
            "taxonomy": taxonomy,
            "source": issue.get("source", ""),
            "prediction": issue.get("prediction", ""),
            "missing_numbers": issue.get("missing_numbers", []),
            "extra_numbers": issue.get("extra_numbers", []),
            "review_status": "PENDING_MANUAL_REVIEW",
        })
    result = {
        "source_benchmark": str(benchmark_file.resolve()),
        "model": model_name,
        "rows": model.get("rows", 0),
        "hard_error_count": model.get("hard_error_count", 0),
        "taxonomy_counts": dict(sorted(counts.items())),
        "records": records,
        "manual_review_required": True,
        "status": "PENDING_MANUAL_REVIEW",
    }
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="current_combined_adapter")
    args = parser.parse_args()
    print(json.dumps(build_report(args.benchmark_file, args.output, args.model), ensure_ascii=False))


if __name__ == "__main__":
    main()
