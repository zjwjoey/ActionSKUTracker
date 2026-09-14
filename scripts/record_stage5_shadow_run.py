"""Record the read-only Stage-5 shadow and 999-row preflight in current reality."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--current-reality", required=True)
    ap.add_argument("--sample-manifest", required=True)
    ap.add_argument("--queue-preflight", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    current_path = Path(args.current_reality).resolve()
    sample_path = Path(args.sample_manifest).resolve()
    queue_path = Path(args.queue_preflight).resolve()
    output = Path(args.output).resolve()
    current = json.loads(current_path.read_text(encoding="utf-8"))
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    evaluation = sample["evaluation"]
    replay_verified = sample_path.exists() and evaluation["pipeline_error"] == 0 and evaluation["duplicate"] == 0
    current["audit_version"] = "stage5-shadow-current-reality-v1"
    current["audit_date"] = datetime.now(timezone.utc).date().isoformat()
    current["head"] = subprocess.run(["git", "-C", str(Path(__file__).resolve().parents[1]), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    current["stage5_shadow"] = {
        "status": "OFFLINE_SHADOW_COMPLETE",
        "sample_manifest": str(sample_path),
        "sample_manifest_sha256": sha(sample_path),
        "sample_rows": evaluation["eligible_sku_count"],
        "sample_field_count": evaluation["field_count"],
        "sample_guard_pass": evaluation["guard_pass"],
        "sample_guard_reject": evaluation["guard_reject"],
        "sample_fact_hallucination_escaped": evaluation["fact_hallucination_escaped"],
        "sample_source_fact_loss_escaped": evaluation["source_fact_loss_escaped"],
        "sample_pipeline_error": evaluation["pipeline_error"],
        "sample_duplicate": evaluation["duplicate"],
        "replay_verified": replay_verified,
        "source_queue_preflight": {
            "manifest": str(queue_path),
            "manifest_sha256": sha(queue_path),
            "rows": queue["total_rows"],
            "batch_count": queue["batch_count"],
            "passed_batches": queue["passed_batches"],
            "failed_batches": queue["failed_batches"],
        },
        "production_writes": False,
        "training_authorized": False,
    }
    current["verdict"] = "STAGE5_OFFLINE_SHADOW_COMPLETE_NO_PRODUCTION_WRITE"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "sample_rows": evaluation["eligible_sku_count"], "queue_rows": queue["total_rows"], "replay_verified": replay_verified}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
