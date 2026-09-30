"""Build a deterministic, source-only 500-row Stage 6 candidate pool."""
from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "runtime/training/qwen3_8b/20260913/stage5_new_source_versions_999/stage5_source_queue_999.jsonl"
PREVIEW = ROOT / "runtime/stage6/20260913/offline_preview_v1"
OUT = ROOT / "runtime/stage6/20260913/source_candidate_500"
sys.path.insert(0, str(ROOT / "src"))
from action_tracker.services.hashing import localization_source_hash  # noqa: E402
ALLOWED_RUNS = {
    "2026-09-07_025601", "2026-09-08_035740", "2026-09-09_032720",
    "2026-09-10_030315", "2026-09-12_024403",
}
FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")
POLLUTION = re.compile(r"<[^>]+>|\b(?:null|undefined)\b|Añadir a tus favoritos|加入收藏", re.I)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def source_hash(source: dict[str, Any]) -> str:
    return localization_source_hash({
        "name_es": source.get("name"), "cat1_es": source.get("cat1"), "cat2_es": source.get("cat2"),
        "spec_es": source.get("spec"), "desc_es": source.get("description"), "details_es": source.get("details"),
    })


def sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    sku = str(row["metadata"]["sku"])
    return (str(row["metadata"]["source_run_id"]), int(sku) if sku.isdigit() else 10**20, sku, row["metadata"]["source_hash"])


def load_rows() -> list[dict[str, Any]]:
    return [json.loads(line) for line in SOURCE.read_text(encoding="utf-8").splitlines() if line.strip()]


def immutable_write(path: Path, payload: bytes) -> None:
    if path.exists():
        existing = path.read_bytes()
        if existing == payload:
            return
        # JSON artifacts are immutable by value.  Accept an equivalent
        # deterministic JSON serialization so a pre-existing frozen manifest
        # with harmless whitespace/newline differences is not rewritten.
        if path.suffix == ".json":
            try:
                if json.loads(existing.decode("utf-8")) == json.loads(payload.decode("utf-8")):
                    return
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
        if path.suffix == ".md":
            if existing.decode("utf-8").replace("\r\n", "\n").rstrip() == payload.decode("utf-8").replace("\r\n", "\n").rstrip():
                return
        raise RuntimeError(f"STAGE6_FROZEN_POOL_ARTIFACT_CHANGED:{path}")
    if not path.exists():
        path.write_bytes(payload)


def quotas(groups: dict[str, list[dict[str, Any]]], total: int) -> dict[str, int]:
    counts = {key: len(value) for key, value in groups.items()}
    exact = {key: value * total / sum(counts.values()) for key, value in counts.items()}
    result = {key: int(value) for key, value in exact.items()}
    remaining = total - sum(result.values())
    for key, _ in sorted(exact.items(), key=lambda item: (-(item[1] - int(item[1])), item[0]))[:remaining]:
        result[key] += 1
    return result


def main() -> int:
    rows = load_rows()
    applied_pairs = set()
    preview_path = PREVIEW / "stage6_apply_preview.jsonl"
    for line in preview_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        applied_pairs.add((str(row.get("sku")), str(row.get("reviewed_source_hash") or "")))
    candidates: list[dict[str, Any]] = []
    excluded = defaultdict(int)
    seen_pairs: set[tuple[str, str]] = set()
    for row in rows:
        meta = row["metadata"]
        source = json.loads(row["messages"][0]["content"])
        pair = (str(meta.get("sku")), str(meta.get("source_hash")))
        if meta.get("source_run_id") not in ALLOWED_RUNS:
            excluded["run_not_allowed"] += 1
            continue
        if meta.get("candidate_status") != "SOURCE_CANDIDATE_READY" or meta.get("source_consistency_status") != "CONSISTENT":
            excluded["source_conflict_or_not_ready"] += 1
            continue
        if pair in applied_pairs:
            excluded["already_stage6_applied_pair"] += 1
            continue
        if pair in seen_pairs:
            excluded["duplicate_pair"] += 1
            continue
        if any(not str(source.get(field) or "").strip() for field in FIELDS):
            excluded["incomplete_source_fields"] += 1
            continue
        if any(POLLUTION.search(str(source.get(field) or "")) for field in FIELDS):
            excluded["source_pollution"] += 1
            continue
        if source_hash(source) != meta.get("source_hash"):
            excluded["source_hash_mismatch"] += 1
            continue
        seen_pairs.add(pair)
        candidate = {
            "messages": [{"role": "user", "content": json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":"))}],
            "metadata": {**meta, "stage6_candidate_status": "SOURCE_ONLY_PENDING_GOLD", "stage6_pool_version": "STAGE6_SOURCE_CANDIDATE_POOL_V1", "production_writes": False, "training_runs": 0},
        }
        candidates.append(candidate)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in sorted(candidates, key=sort_key):
        groups[row["metadata"]["source_run_id"]].append(row)
    if sum(map(len, groups.values())) < 500:
        raise RuntimeError(f"STAGE6_POOL_INSUFFICIENT:{sum(map(len, groups.values()))}")
    q = quotas(groups, 500)
    selected = []
    for run_id in sorted(groups):
        selected.extend(groups[run_id][:q[run_id]])
    selected.sort(key=sort_key)
    if len(selected) != 500 or len({(r["metadata"]["sku"], r["metadata"]["source_hash"]) for r in selected}) != 500:
        raise RuntimeError("STAGE6_POOL_SELECTION_INVALID")
    OUT.mkdir(parents=True, exist_ok=True)
    jsonl = b"".join((json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8") for row in selected)
    immutable_write(OUT / "stage6_source_candidate_500.jsonl", jsonl)
    columns = ("sku", "source_run_id", "source_observed_at", "source_hash", "source_version_status", "prior_sku_seen", "prior_family_seen", "frozen_test_membership", "family_key", "cat1", "cat2", "name", "spec", "description", "details", "candidate_status")
    import io
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for row in selected:
        source = json.loads(row["messages"][0]["content"])
        meta = row["metadata"]
        writer.writerow({
            "sku": meta["sku"], "source_run_id": meta["source_run_id"], "source_observed_at": meta["source_observed_at"],
            "source_hash": meta["source_hash"], "source_version_status": meta["source_version_status"],
            "prior_sku_seen": meta["prior_sku_seen"], "prior_family_seen": meta["prior_family_seen"],
            "frozen_test_membership": json.dumps(meta["frozen_test_membership"], ensure_ascii=False, sort_keys=True),
            "family_key": meta["family_key"], **{field: source.get(field, "") for field in FIELDS},
            "candidate_status": "SOURCE_ONLY_PENDING_GOLD",
        })
    immutable_write(OUT / "stage6_source_candidate_500.csv", ("\ufeff" + stream.getvalue()).encode("utf-8"))
    counts = {run: sum(1 for row in selected if row["metadata"]["source_run_id"] == run) for run in sorted(groups)}
    audit = {
        "contract_id": "STAGE6_SOURCE_CANDIDATE_POOL_V1", "requested_count": 500, "selected_count": len(selected),
        "unique_source_pairs": len({(r["metadata"]["sku"], r["metadata"]["source_hash"]) for r in selected}),
        "source_consistency": "CONSISTENT_ONLY", "run_counts": counts,
        "source_hash_recompute_pass": all(source_hash(json.loads(r["messages"][0]["content"])) == r["metadata"]["source_hash"] for r in selected),
        "prior_sku_populated": all("prior_sku_seen" in r["metadata"] for r in selected),
        "prior_family_populated": all("prior_family_seen" in r["metadata"] for r in selected),
        "frozen_test_membership_populated": all("frozen_test_membership" in r["metadata"] for r in selected),
        "source_conflict_rows_included": 0, "production_writes": 0, "training_runs": 0,
        "excluded_counts": dict(sorted(excluded.items())),
        "status": "READY_FOR_STAGE6_GOLD_REVIEW",
        "required_next_step": "Generate Chinese Gold, run Guard, owner review; no automatic Apply or training.",
    }
    immutable_write(OUT / "manifest.json", (json.dumps(audit, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))
    immutable_write(OUT / "audit.md", (
        "# Stage 6 Source Candidate Pool\n\n"
        f"- Selected: {len(selected)} source-only rows; unique source pairs: {audit['unique_source_pairs']}\n"
        f"- Run counts: {counts}\n"
        "- Source conflicts included: 0; production writes: 0; training runs: 0.\n"
        "- This is a Gold-review input pool, not an approved Apply set.\n"
        f"- Status: `{audit['status']}`\n").encode("utf-8"))
    print(json.dumps(audit, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
