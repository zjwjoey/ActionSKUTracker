"""Build a source-only, contract-ready Stage 5 batch from an isolated source artifact."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from action_tracker.services.hashing import localization_source_hash  # noqa: E402
from action_tracker.stage5.pipeline import FIELDS, canonical_json, sha256_file  # noqa: E402


def _source(row: Mapping[str, Any]) -> dict[str, str]:
    messages = row.get("messages")
    if not isinstance(messages, list):
        raise ValueError("SOURCE_MESSAGES_INVALID")
    user = next((message for message in messages if message.get("role") == "user"), None)
    if user is None:
        raise ValueError("SOURCE_USER_MESSAGE_MISSING")
    try:
        value = json.loads(str(user.get("content") or ""))
    except json.JSONDecodeError as exc:
        raise ValueError("SOURCE_USER_JSON_INVALID") from exc
    if not isinstance(value, dict) or set(value) != set(FIELDS):
        raise ValueError("SOURCE_FIELDS_INVALID")
    return {field: str(value.get(field) or "").strip() for field in FIELDS}


def _source_hash(source: Mapping[str, str]) -> str:
    return localization_source_hash({
        "name_es": source["name"], "cat1_es": source["cat1"], "cat2_es": source["cat2"],
        "spec_es": source["spec"], "desc_es": source["description"], "details_es": source["details"],
    })


def _selection_reasons(metadata: Mapping[str, Any]) -> list[str]:
    explicit = metadata.get("selection_reasons")
    if isinstance(explicit, list) and explicit:
        return sorted({str(item).strip().upper() for item in explicit if str(item).strip()})
    status = str(metadata.get("candidate_status") or "").strip().upper()
    if status in {"REVIEW_REQUIRED", "NEEDS_REVIEW", "PENDING_MANUAL_REVIEW"}:
        return ["NEEDS_REVIEW"]
    if str(metadata.get("is_new") or "").strip().lower() in {"1", "true", "yes"}:
        return ["NEW"]
    raise ValueError("SELECTION_REASON_MISSING")


def recorded_candidate_hash(source_manifest: Mapping[str, Any]) -> str:
    """Read either supported historical manifest shape without guessing."""

    artifacts = source_manifest.get("artifacts") or {}
    if not isinstance(artifacts, Mapping):
        raise ValueError("SOURCE_MANIFEST_ARTIFACTS_INVALID")
    candidate = artifacts.get("candidate_jsonl")
    if isinstance(candidate, Mapping):
        return str(candidate.get("sha256") or "").strip().lower()
    return str(artifacts.get("candidate_jsonl_sha256") or "").strip().lower()


def load_excluded_skus(paths: Iterable[Path]) -> set[str]:
    output: set[str] = set()
    for path in paths:
        if not path:
            continue
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            sku = str((row.get("metadata") or {}).get("sku") or row.get("sku") or "").strip()
            if sku:
                output.add(sku)
    return output


def build_rows(
    rows: Iterable[Mapping[str, Any]], *, batch_id: str, run_id: str,
    observation_date: str, exclude_skus: set[str] | None = None, limit: int = 0,
) -> list[dict[str, Any]]:
    excluded = exclude_skus or set()
    output: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in rows:
        metadata = raw.get("metadata") or {}
        sku = str(metadata.get("sku") or "").strip()
        if not sku or sku in excluded:
            continue
        source = _source(raw)
        digest = _source_hash(source)
        supplied = str(metadata.get("source_hash") or "").strip().lower()
        if supplied and supplied != digest:
            raise ValueError(f"SOURCE_HASH_MISMATCH sku={sku}")
        key = (sku, digest)
        if key in output:
            raise ValueError(f"DUPLICATE_SOURCE_ROW sku={sku}")
        output[key] = {
            "messages": [{"role": "user", "content": canonical_json(source)}],
            "metadata": {
                "batch_id": batch_id,
                "run_id": run_id,
                "observation_date": observation_date,
                "sku": sku,
                "canonical_id": str(metadata.get("canonical_id") or sku).strip(),
                "source_hash": digest,
                "selection_reasons": _selection_reasons(metadata),
                "source_artifact_collection": str(metadata.get("collection") or ""),
            },
        }
    ordered = [output[key] for key in sorted(output)]
    return ordered[:limit] if limit else ordered


def _atomic_create(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() == content:
            return
        raise FileExistsError(f"IMMUTABLE_ARTIFACT_CONFLICT: {path}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--observation-date", required=True)
    parser.add_argument("--exclude-input", action="append", type=Path, default=[])
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    source = args.source if args.source.is_absolute() else ROOT / args.source
    source_manifest_path = args.source_manifest if args.source_manifest.is_absolute() else ROOT / args.source_manifest
    output = args.output if args.output.is_absolute() else ROOT / args.output
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    recorded_hash = recorded_candidate_hash(source_manifest)
    if recorded_hash and recorded_hash != sha256_file(source):
        raise SystemExit("SOURCE_MANIFEST_HASH_MISMATCH")
    source_rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    excluded = load_excluded_skus([
        item if item.is_absolute() else ROOT / item for item in args.exclude_input
    ])
    rows = build_rows(
        source_rows, batch_id=args.batch_id, run_id=args.run_id,
        observation_date=args.observation_date, exclude_skus=excluded, limit=args.limit,
    )
    if not rows:
        raise SystemExit("EMPTY_STAGE5_BATCH")
    content = ("".join(canonical_json(row) + "\n" for row in rows)).encode("utf-8")
    _atomic_create(output, content)
    manifest = {
        "manifest_version": "stage5-source-batch-v1",
        "batch_id": args.batch_id,
        "run_id": args.run_id,
        "observation_date": args.observation_date,
        "rows": len(rows),
        "source": {"path": str(source.resolve()), "sha256": sha256_file(source)},
        "source_manifest": {"path": str(source_manifest_path.resolve()), "sha256": sha256_file(source_manifest_path)},
        "excluded_sku_count": len(excluded),
        "output": {"path": str(output.resolve()), "sha256": sha256_file(output)},
        "assistant_reference_messages_removed": True,
        "training_labels_used": False,
    }
    manifest_path = output.with_suffix(".manifest.json")
    _atomic_create(manifest_path, (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
