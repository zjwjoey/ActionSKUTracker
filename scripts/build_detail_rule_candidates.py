"""Aggregate a detail review queue into source-bound rule candidates."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from action_tracker.translation.detail_rule_candidates import build_detail_rule_candidates  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    with args.queue.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise SystemExit("DETAIL_RULE_QUEUE_EMPTY")
    first = rows[0]
    candidates = build_detail_rule_candidates(
        rows,
        source_sha256=str(first.get("source_sha256") or ""),
        target_sha256=str(first.get("target_sha256") or ""),
        rules_sha256=str(first.get("detail_rules_sha256") or ""),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({
        "schema": "ACTION_DETAIL_RULE_CANDIDATES_V1",
        "source_sha256": first.get("source_sha256", ""),
        "target_sha256": first.get("target_sha256", ""),
        "detail_rules_sha256": first.get("detail_rules_sha256", ""),
        "candidate_count": len(candidates),
        "auto_apply": False,
        "candidates": candidates,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "candidate_count": len(candidates), "auto_apply": False}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
