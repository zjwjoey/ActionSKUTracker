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
from action_tracker.translation.regression_cases import load_regression_cases  # noqa: E402

csv.field_size_limit(10 * 1024 * 1024)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", required=True, type=Path)
    parser.add_argument("--decisions", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--candidates", type=Path)
    parser.add_argument(
        "--regressions", type=Path,
        default=ROOT / "data/qa/localization_regressions_v1.jsonl",
    )
    args = parser.parse_args()
    with args.queue.open("r", encoding="utf-8-sig", newline="") as handle:
        queue = list(csv.DictReader(handle))
    with args.decisions.open("r", encoding="utf-8-sig", newline="") as handle:
        decisions = list(csv.DictReader(handle))
    if not queue:
        raise SystemExit("DETAIL_RULE_QUEUE_EMPTY")
    first = queue[0]
    if not args.regressions.exists():
        raise SystemExit(f"REGRESSION_CORPUS_MISSING:{args.regressions}")
    regressions = load_regression_cases(args.regressions)
    candidate_rows = None
    if args.candidates:
        candidate_payload = json.loads(args.candidates.read_text(encoding="utf-8"))
        expected_hashes = {
            "source_sha256": str(first.get("source_sha256") or ""),
            "target_sha256": str(first.get("target_sha256") or ""),
            "detail_rules_sha256": str(first.get("detail_rules_sha256") or ""),
        }
        if any(str(candidate_payload.get(key) or "") != value for key, value in expected_hashes.items()):
            raise SystemExit("DETAIL_RULE_CANDIDATE_HASH_MISMATCH")
        candidate_rows = candidate_payload.get("candidates")
        if not isinstance(candidate_rows, list):
            raise SystemExit("DETAIL_RULE_CANDIDATE_SCHEMA_INVALID")
    report = build_detail_rule_proposals(
        queue, decisions,
        source_sha256=str(first.get("source_sha256") or ""),
        target_sha256=str(first.get("target_sha256") or ""),
        rules_sha256=str(first.get("detail_rules_sha256") or ""),
        regression_cases=regressions,
        require_regression=True,
        candidate_rows=candidate_rows,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "accepted": report["accepted"], "proposal_count": len(report["proposals"]), "rules_write": False}, ensure_ascii=True))
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
