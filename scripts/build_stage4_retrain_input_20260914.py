"""Build a non-production targeted-retrain input from approved remediation Gold.

This artifact intentionally records both the fresh, disjoint Stage 5 Gold and
the separately owner-confirmed Stage 4 remediation Gold.  It never writes
Master, SQLite, or dictionary data; the resulting manifest keeps the historical
overlap visible so the formal release controller can make the final decision.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold42", required=True)
    ap.add_argument("--fresh-train", required=True)
    ap.add_argument("--fresh-validation", required=True)
    ap.add_argument("--output-dir", required=True)
    args = ap.parse_args()
    gold42 = Path(args.gold42).resolve()
    fresh_train = Path(args.fresh_train).resolve()
    fresh_validation = Path(args.fresh_validation).resolve()
    out = Path(args.output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    gold_rows = read_rows(gold42)
    fresh_rows = read_rows(fresh_train)
    validation_rows = read_rows(fresh_validation)
    train = gold_rows + fresh_rows
    train_path = out / "stage4_targeted_retrain_train_20260914.jsonl"
    val_path = out / "stage4_targeted_retrain_validation_20260914.jsonl"
    train_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in train), encoding="utf-8")
    val_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in validation_rows), encoding="utf-8")
    manifest = {
        "schema": "STAGE4_TARGETED_RETRAIN_INPUT_V1",
        "gold42_rows": len(gold_rows),
        "fresh_train_rows": len(fresh_rows),
        "train_rows": len(train),
        "validation_rows": len(validation_rows),
        "gold42_sha256": sha256(gold42),
        "fresh_train_sha256": sha256(fresh_train),
        "fresh_validation_sha256": sha256(fresh_validation),
        "train_sha256": sha256(train_path),
        "validation_sha256": sha256(val_path),
        "historical_overlap_note": "Gold42 historical overlap is retained as an explicit audit fact; this package is not formal-release-authorized by itself.",
        "production_writes": False,
    }
    (out / "stage4_targeted_retrain_input_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"train": str(train_path), "validation": str(val_path), "manifest": str(out / 'stage4_targeted_retrain_input_manifest.json'), "train_rows": len(train), "validation_rows": len(validation_rows)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
