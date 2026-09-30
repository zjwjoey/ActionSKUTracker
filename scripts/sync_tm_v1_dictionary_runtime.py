"""Synchronize the approved TM manual overrides into the runtime dictionary."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def row_count(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--baseline-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    source = args.source_dir.resolve() / "manual_overrides.csv"
    runtime = args.runtime_dir.resolve() / "manual_overrides.csv"
    manifest_path = args.baseline_manifest.resolve()
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    before_runtime = out / "runtime_manual_overrides.before.csv"
    shutil.copy2(runtime, before_runtime)
    source_hash = sha256_file(source)
    runtime_before_hash = sha256_file(runtime)
    staged = runtime.with_name(f".{runtime.name}.{uuid.uuid4().hex}.tmp")
    shutil.copy2(source, staged)
    os.replace(staged, runtime)
    runtime_after_hash = sha256_file(runtime)
    if runtime_after_hash != source_hash:
        raise SystemExit("RUNTIME_SYNC_HASH_MISMATCH")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.setdefault("files", {}).setdefault("manual_overrides.csv", {})
    manifest["files"]["manual_overrides.csv"] = {"rows": row_count(source), "sha256": source_hash}
    manifest["published_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    staged_manifest = manifest_path.with_name(f".{manifest_path.name}.{uuid.uuid4().hex}.tmp")
    staged_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(staged_manifest, manifest_path)
    result = {
        "artifact_type": "ACTION_TM_V1_RUNTIME_DICTIONARY_SYNC",
        "source_file": str(source),
        "runtime_file": str(runtime),
        "before_snapshot": str(before_runtime),
        "source_sha256": source_hash,
        "runtime_before_sha256": runtime_before_hash,
        "runtime_after_sha256": runtime_after_hash,
        "rows": row_count(source),
        "baseline_manifest": str(manifest_path),
        "production_write": True,
        "status": "PASS",
    }
    (out / "tm_v1_runtime_dictionary_sync_manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "TM_V1_RUNTIME_DICTIONARY_SYNC.md").write_text("\n".join([
        "# TM V1 Runtime Dictionary Sync", "",
        "仅同步已批准的 manual_overrides.csv；其他字典文件未修改。", "",
        f"- rows: {result['rows']}", f"- runtime before: {runtime_before_hash}",
        f"- runtime after: {runtime_after_hash}", f"- status: {result['status']}", "",
    ]) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
