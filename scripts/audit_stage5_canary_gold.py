"""Run the final offline Guard/leakage/replay audit for Stage 5 canary Gold."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_ingestion(script: Path, args: list[str]) -> None:
    subprocess.run([sys.executable, str(script), *args], check=True, capture_output=True, text=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ingestion-script", type=Path, required=True)
    parser.add_argument("--source-input", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--owner-decisions", type=Path, required=True)
    parser.add_argument("--retry-manifest", type=Path, required=True)
    parser.add_argument("--historical", nargs="+", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir
    files = {
        "gold_evidence": output / "stage5_canary_gold_evidence_9.jsonl",
        "source_only": output / "stage5_canary_gold_source_only_9.jsonl",
        "training": output / "stage5_canary_gold_training_9.jsonl",
    }
    command_args = [
        "--source-input", str(args.source_input), "--candidates", str(args.candidates),
        "--owner-decisions", str(args.owner_decisions), "--retry-manifest", str(args.retry_manifest),
        "--historical", *[str(path) for path in args.historical], "--output-dir", str(output),
    ]
    before = {name: sha256_file(path) for name, path in files.items() if path.exists()}
    run_ingestion(args.ingestion_script, command_args)
    first = {name: sha256_file(path) for name, path in files.items()}
    run_ingestion(args.ingestion_script, command_args)
    second = {name: sha256_file(path) for name, path in files.items()}
    manifest_path = output / "stage5_canary_gold_ingestion_9.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_hashes = {name: manifest["outputs"][name if name != "gold_evidence" else "gold_evidence"]["sha256"] for name in files}
    checks = {
        "gold_rows_9": manifest.get("counts", {}).get("gold_rows") == 9,
        "gold_fields_54": manifest.get("counts", {}).get("gold_fields") == 54,
        "guard_executed_54": manifest.get("guard", {}).get("executed_fields") == 54,
        "non_exception_guard_rejects_0": manifest.get("guard", {}).get("rejected_fields_except_explicit_owner_exception") == 0,
        "source_pair_overlap_0": manifest.get("counts", {}).get("historical_source_pair_overlap") == 0,
        "output_hashes_match_manifest": all(first[name] == manifest_hashes[name] for name in files),
        "replay_hashes_stable": first == second and (not before or before == first),
        "production_writes_all_false": all(not value for value in manifest.get("production_writes", {}).values()),
    }
    result = "PASS" if all(checks.values()) else "FAIL"
    audit = {
        "audit_version": "stage5-canary-gold-final-audit-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ingestion_manifest": str(manifest_path.resolve()),
        "checks": checks,
        "hashes": {"before": before, "first": first, "second": second, "manifest": manifest_hashes},
        "production_write": False,
        "result": result,
    }
    audit_path = output / "stage5_canary_gold_final_audit.json"
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_path = output / "STAGE5_CANARY_GOLD_FINAL_AUDIT.md"
    report_path.write_text("\n".join([
        "# Stage 5 Canary Gold Final Audit",
        "",
        f"- 结果：`{result}`",
        f"- Gold SKU：`{manifest.get('counts', {}).get('gold_rows')}`",
        f"- Gold 字段：`{manifest.get('counts', {}).get('gold_fields')}`",
        f"- 守卫执行：`{manifest.get('guard', {}).get('executed_fields')}`",
        f"- source-pair 重叠：`{manifest.get('counts', {}).get('historical_source_pair_overlap')}`",
        f"- replay 输出 hash 稳定：`{checks['replay_hashes_stable']}`",
        "- 生产写入：`False`",
        "",
    ]) + "\n", encoding="utf-8")
    print(json.dumps({"result": result, "audit": str(audit_path.resolve()), "checks": checks}, ensure_ascii=False))
    return 0 if result == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
