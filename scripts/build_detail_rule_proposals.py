"""Validate owner detail decisions and emit non-mutating rule proposals."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from action_tracker.translation.detail_rule_candidates import build_detail_rule_proposals  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", required=True, type=Path)
    parser.add_argument("--decisions", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    with args.queue.open("r", encoding="utf-8-sig", newline="") as handle:
        queue = list(csv.DictReader(handle))
    with args.decisions.open("r", encoding="utf-8-sig", newline="") as handle:
        decisions = list(csv.DictReader(handle))
    if not queue:
        raise SystemExit("DETAIL_RULE_QUEUE_EMPTY")
    first = queue[0]
    report = build_detail_rule_proposals(
        queue, decisions,
        source_sha256=str(first.get("source_sha256") or ""),
        target_sha256=str(first.get("target_sha256") or ""),
        rules_sha256=str(first.get("detail_rules_sha256") or ""),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "accepted": report["accepted"], "proposal_count": len(report["proposals"]), "rules_write": False}, ensure_ascii=True))
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
