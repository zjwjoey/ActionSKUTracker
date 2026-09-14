"""Run Stage-5 preflight once per source queue batch.

The Stage-5 contract intentionally forbids mixed `(batch_id, run_id,
observation_date)` input.  This wrapper preserves that boundary while auditing
the complete multi-run source-version queue without invoking a model or writing
production data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output-dir", required=True)
    args = ap.parse_args()
    source = Path(args.input).resolve()
    output = Path(args.output_dir).resolve()
    rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    groups: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for row in rows:
        metadata = row.get("metadata") or {}
        key = (str(metadata.get("batch_id")), str(metadata.get("run_id")), str(metadata.get("observation_date")))
        groups[key].append(row)
    output.mkdir(parents=True, exist_ok=True)
    results = []
    pipeline = Path(__file__).resolve().parent / "run_stage5_pipeline.py"
    for index, (identity, group) in enumerate(sorted(groups.items()), start=1):
        batch_id, run_id, observation_date = identity
        safe = f"{observation_date}_{run_id.replace(':', '').replace(' ', '_')}"
        group_dir = output / f"batch_{index:02d}_{safe}"
        group_input = group_dir / "source_only.jsonl"
        group_input.parent.mkdir(parents=True, exist_ok=True)
        group_input.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in group), encoding="utf-8")
        command = [sys.executable, str(pipeline), "--input", str(group_input), "--output-dir", str(group_dir / "preflight"), "--preflight-only"]
        process = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        result = {
            "batch_id": batch_id,
            "run_id": run_id,
            "observation_date": observation_date,
            "rows": len(group),
            "input": str(group_input),
            "input_sha256": sha(group_input),
            "returncode": process.returncode,
            "stdout": process.stdout,
            "stderr": process.stderr,
        }
        if process.returncode == 0:
            try:
                result["preflight"] = json.loads(process.stdout.strip().splitlines()[-1])
            except (json.JSONDecodeError, IndexError):
                result["preflight_parse"] = "FAILED"
        results.append(result)
    passed = sum(item["returncode"] == 0 for item in results)
    manifest = {
        "schema": "STAGE5_SOURCE_QUEUE_PREFLIGHT_V1",
        "input": str(source),
        "input_sha256": sha(source),
        "total_rows": len(rows),
        "batch_count": len(results),
        "passed_batches": passed,
        "failed_batches": len(results) - passed,
        "groups": results,
        "model_invocations": 0,
        "production_writes": False,
        "training_authorized": False,
    }
    manifest_path = output / "stage5_source_queue_preflight_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"total_rows": len(rows), "batch_count": len(results), "passed_batches": passed, "failed_batches": len(results) - passed, "manifest": str(manifest_path)}, ensure_ascii=False))
    return 0 if passed == len(results) and len(rows) == 999 else 1


if __name__ == "__main__":
    raise SystemExit(main())
