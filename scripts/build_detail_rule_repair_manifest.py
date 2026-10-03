"""Expand approved detail-rule proposals into a non-mutating repair manifest."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from action_tracker.localization.repair_service import build_preview, policy_hash  # noqa: E402
from action_tracker.translation.detail_rule_repair import build_detail_rule_repair_manifest  # noqa: E402


def _jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", required=True, type=Path)
    parser.add_argument("--proposals", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    proposal_payload = json.loads(args.proposals.read_text(encoding="utf-8"))
    policy = json.loads(args.policy.read_text(encoding="utf-8"))
    records = _jsonl(args.records)
    result = build_detail_rule_repair_manifest(
        records, proposal_payload.get("proposals", []),
        policy_manifest_hash=policy_hash(policy),
    )
    result["preview_rows"] = build_preview(
        records, result["rows"], expected_policy_hash=result["policy_manifest_hash"],
    )
    result["preview_status_counts"] = {
        status: sum(1 for row in result["preview_rows"] if row.get("status") == status)
        for status in sorted({str(row.get("status") or "") for row in result["preview_rows"]})
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "matched_occurrences": result["matched_occurrences"],
        "blocked_occurrences": result["blocked_occurrences"],
        "preview_status_counts": result["preview_status_counts"],
        "production_apply": False,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
