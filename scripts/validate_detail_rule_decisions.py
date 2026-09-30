"""Validate owner decisions for DETAIL_RULE_REVIEW.csv without applying them."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from action_tracker.translation.detail_review_contract import validate_detail_rule_decisions  # noqa: E402


def _read_csv(path: Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", required=True, type=Path)
    parser.add_argument("--decisions", required=True, type=Path)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--target-sha256", required=True)
    parser.add_argument("--rules-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = validate_detail_rule_decisions(
        _read_csv(args.queue), _read_csv(args.decisions),
        source_sha256=args.source_sha256,
        target_sha256=args.target_sha256,
        rules_sha256=args.rules_sha256,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "accepted": result["accepted"],
        "errors": len(result["errors"]),
        "approved_rows": len(result["approved_rows"]),
        "rejected_rows": len(result["rejected_rows"]),
        "auto_apply": result["auto_apply"],
        "rules_write": result["rules_write"],
        "output": str(args.output),
    }, ensure_ascii=True))
    return 0 if result["accepted"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
