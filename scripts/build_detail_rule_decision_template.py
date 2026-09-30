"""Build a blank, hash-bound owner decision template for detail rule reviews."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path


COLUMNS = (
    "review_id", "decision", "approved_target", "reviewer", "reviewed_at",
    "evidence_status", "source_sha256", "target_sha256", "detail_rules_sha256",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", required=True, type=Path)
    parser.add_argument("--queue-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    queue_manifest = json.loads(args.queue_manifest.read_text(encoding="utf-8"))
    with args.queue.open("r", encoding="utf-8-sig", newline="") as handle:
        queue_rows = list(csv.DictReader(handle))
    seen: set[str] = set()
    review_ids: list[str] = []
    for row in queue_rows:
        review_id = str(row.get("review_id") or "").strip()
        if review_id and review_id not in seen:
            seen.add(review_id)
            review_ids.append(review_id)
    rows = [
        {
            "review_id": review_id, "decision": "", "approved_target": "",
            "reviewer": "", "reviewed_at": "", "evidence_status": "",
            "source_sha256": str(queue_manifest.get("source_sha256") or ""),
            "target_sha256": str(queue_manifest.get("target_sha256") or ""),
            "detail_rules_sha256": str(queue_manifest.get("detail_rules_sha256") or ""),
        }
        for review_id in sorted(review_ids)
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    manifest = {
        "schema": "ACTION_DETAIL_RULE_DECISION_TEMPLATE_V1",
        "artifact": args.output.name,
        "queue_artifact": args.queue.name,
        "queue_sha256": _sha256(args.queue),
        "queue_manifest_sha256": _sha256(args.queue_manifest),
        "row_count": len(rows),
        "review_only": True,
        "auto_apply": False,
        "rules_write": False,
    }
    manifest_path = args.output.with_suffix(args.output.suffix + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "manifest": str(manifest_path), "row_count": len(rows), "auto_apply": False}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
