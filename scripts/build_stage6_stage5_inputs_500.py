"""Prepare the five frozen-contract Stage 5 inputs for the 500-row pool."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "runtime/stage6/20260913/source_candidate_500/stage6_source_candidate_500.jsonl"
OUT = ROOT / "runtime/stage6/20260913/gold_model_inputs_500"
FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def immutable_write(path: Path, payload: bytes) -> None:
    if path.exists():
        if path.read_bytes() != payload:
            raise RuntimeError(f"STAGE6_MODEL_INPUT_CHANGED:{path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def main() -> int:
    rows = [json.loads(line) for line in SOURCE.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(rows) != 500:
        raise ValueError(f"SOURCE_COUNT_INVALID:{len(rows)}")
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        meta = row["metadata"]
        source = json.loads(row["messages"][0]["content"])
        run_id = str(meta["source_run_id"])
        metadata = {
            "batch_id": f"stage6_source_candidate_{run_id}",
            "run_id": run_id,
            "observation_date": run_id.split("_", 1)[0],
            "sku": str(meta["sku"]),
            "canonical_id": str(meta["sku"]),
            "source_hash": str(meta["source_hash"]),
            "selection_reasons": [str(meta.get("source_version_status") or "SOURCE_HASH_CHANGED")],
        }
        grouped[run_id].append({
            "messages": [{"role": "user", "content": canonical(source)}],
            "metadata": metadata,
        })
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {"contract_id": "STAGE6_FROZEN_MODEL_INPUTS_V1", "source": str(SOURCE), "batches": {}}
    for run_id in sorted(grouped):
        batch = sorted(grouped[run_id], key=lambda item: int(item["metadata"]["sku"]))
        path = OUT / f"stage6_input_{run_id}.jsonl"
        content = b"".join((canonical(row) + "\n").encode("utf-8") for row in batch)
        immutable_write(path, content)
        manifest["batches"][run_id] = {"count": len(batch), "path": str(path), "batch_id": batch[0]["metadata"]["batch_id"]}
    immutable_write(OUT / "manifest.json", (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
