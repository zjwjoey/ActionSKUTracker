"""Build the Stage 5 Source-Version Candidate Queue v2.

This command creates a source-only, offline candidate pool.  It never writes
Master, SQLite, the dictionary, model adapters, or production state.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from action_tracker.stage5.source_candidate_v2 import (
    FAMILY_KEY_METHOD,
    SOURCE_HASH_ALGORITHM,
    SOURCE_HASH_CONTRACT_VERSION,
    SOURCE_FIELDS,
    SOURCE_FIELD_KEYS,
    consistency_status,
    family_key,
    source_consistency_flags,
    source_consistency_rules_manifest,
    source_from_mapping,
    source_hash,
    source_quality_issues,
)

SOURCE_CONSISTENCY_RULES = source_consistency_rules_manifest()

EXPECTED_RUNS = (
    "2026-09-07_025601",
    "2026-09-08_035740",
    "2026-09-09_032720",
    "2026-09-10_030315",
    "2026-09-12_024403",
)
EXPECTED_BATCH_COUNTS = {
    "2026-09-07_025601": 9,
    "2026-09-08_035740": 7,
    "2026-09-09_032720": 108,
    "2026-09-10_030315": 6,
    "2026-09-12_024403": 869,
}


def is_historical_split(path: Path) -> bool:
    return path.name.endswith(("_train.jsonl", "_validation.jsonl", "_test.jsonl")) or "stage4_test_only_485" in path.name


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def jsonl_rows(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def parse_source_message(row: dict[str, Any]) -> dict[str, str] | None:
    messages = row.get("messages") or []
    user_messages = [message for message in messages if message.get("role") == "user"]
    if not user_messages:
        return None
    try:
        value = json.loads(str(user_messages[0].get("content") or ""))
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict) or set(value) != set(SOURCE_FIELDS):
        return None
    return source_from_mapping(value)


def historical_sets(training_root: Path, *, before_date: str | None = None) -> dict[str, Any]:
    """Read splits available before a queue's selection date.

    Later Stage 5 training may legitimately consume this queue.  Counting those
    later splits as *prior* history would retroactively invalidate its source
    selection and make a replay depend on when it is run.
    """
    pair_set: set[tuple[str, str]] = set()
    sku_set: set[str] = set()
    family_set: set[str] = set()
    frozen_skus: set[str] = set()
    frozen_families: set[str] = set()
    for path in training_root.rglob("*.jsonl"):
        if not is_historical_split(path):
            continue
        if before_date is not None:
            date_component = path.relative_to(training_root).parts[0]
            if date_component.isdigit() and len(date_component) == 8 and date_component >= before_date:
                continue
        is_frozen = "stage4_test_only_485" in path.name
        for row in jsonl_rows(path):
            metadata = row.get("metadata") or {}
            sku = str(metadata.get("sku") or "").strip()
            source_hash_value = str(metadata.get("source_hash") or "").strip().lower()
            if not sku:
                continue
            sku_set.add(sku)
            if source_hash_value:
                pair_set.add((sku, source_hash_value))
            source = parse_source_message(row)
            key = family_key(source) if source else ""
            if key:
                family_set.add(key)
            if is_frozen:
                frozen_skus.add(sku)
                if key:
                    frozen_families.add(key)
    return {
        "pairs": pair_set,
        "skus": sku_set,
        "families": family_set,
        "frozen_skus": frozen_skus,
        "frozen_families": frozen_families,
    }


def run_evidence(run_id: str) -> dict[str, Any]:
    base = ROOT / "runtime" / "snapshots" / run_id[:10] / run_id
    qa = json.loads((base / "qa_report.json").read_text(encoding="utf-8"))
    report = json.loads((base / "run_report.json").read_text(encoding="utf-8"))
    manifest = json.loads((base / "run_manifest.json").read_text(encoding="utf-8"))
    issues: list[str] = []
    requirements = {
        "qa_passed": qa.get("passed") is True,
        "commit_status": report.get("commit_status") == "FULL_COMMIT",
        "observation_complete": report.get("observation_complete") is True,
        "presence_mode": report.get("presence_mode") == "FULL",
        "presence_access_state": report.get("presence_access_state") == "NORMAL",
        "final_access_state": report.get("final_access_state") == "NORMAL",
    }
    for field, passed in requirements.items():
        if not passed:
            issues.append(field)
    return {
        "run_id": run_id,
        "qa_status": qa.get("state"),
        "qa_passed": qa.get("passed"),
        "commit_mode": report.get("commit_status"),
        "observation_complete": report.get("observation_complete"),
        "presence_mode": report.get("presence_mode"),
        "presence_status": report.get("presence_access_state"),
        "detail_status": report.get("detail_access_state"),
        "final_status": report.get("final_access_state"),
        "detail_result": report.get("detail_status"),
        "blocked_stage": report.get("blocked_stage"),
        "transient_error_count": manifest.get("transient_error_count"),
        "source_observed_at": manifest.get("started_at"),
        "eligible": not issues,
        "issues": issues,
    }


def read_products() -> dict[str, dict[str, str]]:
    with sqlite3.connect(ROOT / "runtime" / "db" / "action_tracker.db") as conn:
        return {
            str(row[0]): {"first_seen_at": str(row[1] or ""), "canonical_id": str(row[2] or "")}
            for row in conn.execute("SELECT official_sku, first_seen_at, canonical_id FROM products")
        }


def read_blocked_skus() -> set[str]:
    path = ROOT / "runtime" / "dictionary" / "source_damage_report.csv"
    if not path.exists():
        return set()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {
            str(row.get("sku") or "").strip()
            for row in csv.DictReader(handle)
            if str(row.get("status") or "").strip() in {"SOURCE_DAMAGED", "SOURCE_POLLUTED"}
        }


def build_candidates(old: dict[str, Any], evidence: dict[str, dict[str, Any]], products: dict[str, dict[str, str]], blocked_skus: set[str]) -> tuple[list[dict[str, Any]], Counter[str]]:
    candidates: dict[tuple[str, str], dict[str, Any]] = {}
    exclusions: Counter[str] = Counter()
    for run_id in EXPECTED_RUNS:
        path = ROOT / "runtime" / "snapshots" / run_id[:10] / run_id / "products_normalized.csv"
        if not path.exists():
            raise SystemExit(f"SNAPSHOT_CSV_MISSING:{run_id}")
        if not evidence[run_id]["eligible"]:
            raise SystemExit(f"UNEXPECTED_UNTRUSTED_RUN:{run_id}:{evidence[run_id]['issues']}")
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for snapshot_row in csv.DictReader(handle):
                sku = str(snapshot_row.get("sku") or "").strip()
                if not sku:
                    exclusions["SKU_EMPTY"] += 1
                    continue
                if sku in blocked_skus:
                    exclusions["SOURCE_DAMAGE_BLOCK"] += 1
                    continue
                source = source_from_mapping({field: snapshot_row.get(key) for field, key in SOURCE_FIELD_KEYS.items()})
                quality_issues = source_quality_issues(source, sku)
                if quality_issues:
                    exclusions[quality_issues[0]] += 1
                    continue
                digest = source_hash(source)
                key = (sku, digest)
                if key in old["pairs"]:
                    exclusions["HISTORICAL_EXACT_SOURCE_OVERLAP"] += 1
                    continue
                product = products.get(sku, {})
                status = "SOURCE_HASH_CHANGED" if sku in old["skus"] else "NEW_SKU_NEW_SOURCE"
                consistency_flags = source_consistency_flags(source)
                candidate = {
                    "sku": sku,
                    "canonical_id": str(snapshot_row.get("canonical_id") or product.get("canonical_id") or f"ACT{sku}"),
                    "source_run_id": run_id,
                    "source_snapshot_path": str(path.resolve()),
                    "source_observed_at": evidence[run_id]["source_observed_at"],
                    "observation_date": run_id[:10],
                    "source": source,
                    "source_hash": digest,
                    "source_version_status": status,
                    "prior_pair_seen": False,
                    "prior_sku_seen": sku in old["skus"],
                    "prior_family_seen": family_key(source) in old["families"],
                    "frozen_test_membership": {
                        "sku_seen": sku in old["frozen_skus"],
                        "family_seen": family_key(source) in old["frozen_families"],
                    },
                    "family_key": family_key(source),
                    "family_key_method": FAMILY_KEY_METHOD,
                    "source_consistency_status": consistency_status(consistency_flags),
                    "source_consistency_flags": consistency_flags,
                    "candidate_status": "SOURCE_CONFLICT_REVIEW" if consistency_flags else "SOURCE_CANDIDATE_READY",
                    "selection_reasons": ["NEW" if status == "NEW_SKU_NEW_SOURCE" else "SOURCE_HASH_CHANGED"],
                    "first_seen_at": product.get("first_seen_at", ""),
                    "run_evidence": evidence[run_id],
                }
                previous = candidates.get(key)
                if previous is None or run_id > previous["source_run_id"]:
                    candidates[key] = candidate
    rows = list(candidates.values())
    rows.sort(key=lambda row: (
        0 if row["source_version_status"] == "NEW_SKU_NEW_SOURCE" else 1,
        -int(row["source_run_id"].replace("-", "").replace("_", "")),
        int(row["sku"]) if row["sku"].isdigit() else 10**18,
        row["source_hash"],
    ))
    return rows, exclusions


def select_run_quotas(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply the frozen v2 per-run quotas to the clean candidate pool."""

    selected: list[dict[str, Any]] = []
    for run_id in EXPECTED_RUNS:
        available = [row for row in rows if row["source_run_id"] == run_id]
        available.sort(key=lambda row: (
            0 if row["source_version_status"] == "NEW_SKU_NEW_SOURCE" else 1,
            int(row["sku"]) if row["sku"].isdigit() else 10**18,
            row["source_hash"],
        ))
        quota = EXPECTED_BATCH_COUNTS[run_id]
        if len(available) < quota:
            raise SystemExit(f"RUN_QUOTA_UNAVAILABLE:{run_id}:need={quota}:got={len(available)}")
        selected.extend(available[:quota])
    selected.sort(key=lambda row: (
        row["source_run_id"],
        int(row["sku"]) if row["sku"].isdigit() else 10**18,
        row["source_hash"],
    ))
    return selected


def metadata_for(row: dict[str, Any], batch_id: str) -> dict[str, Any]:
    return {
        "batch_id": batch_id,
        "run_id": row["source_run_id"],
        "source_run_id": row["source_run_id"],
        "observation_date": row["observation_date"],
        "source_observed_at": row["source_observed_at"],
        "source_snapshot_path": row["source_snapshot_path"],
        "sku": row["sku"],
        "canonical_id": row["canonical_id"],
        "source_hash": row["source_hash"],
        "source_hash_algorithm": SOURCE_HASH_ALGORITHM,
        "source_hash_contract_version": SOURCE_HASH_CONTRACT_VERSION,
        "selection_reasons": row["selection_reasons"],
        "source_version_status": row["source_version_status"],
        "prior_pair_seen": row["prior_pair_seen"],
        "prior_sku_seen": row["prior_sku_seen"],
        "prior_family_seen": row["prior_family_seen"],
        "frozen_test_membership": row["frozen_test_membership"],
        "family_key": row["family_key"],
        "family_key_method": row["family_key_method"],
        "source_consistency_status": row["source_consistency_status"],
        "source_consistency_flags": row["source_consistency_flags"],
        "source_consistency_rules_version": SOURCE_CONSISTENCY_RULES["version"],
        "source_consistency_rules_sha256": SOURCE_CONSISTENCY_RULES["sha256"],
        "candidate_status": row["candidate_status"],
        "candidate_status_scope": "SOURCE_ONLY_AWAITING_HUMAN_GOLD",
        "confidence_status": "SOURCE_VERSION_CANDIDATE",
        "production_writes": False,
        "training_eligible": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="2026-09-13")
    parser.add_argument("--limit", type=int, default=999)
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    date.fromisoformat(args.date)
    if args.limit != 999:
        raise SystemExit("STAGE5_V2_LIMIT_MUST_BE_999")
    output_dir = Path(args.output_dir) if args.output_dir else ROOT / "runtime" / "training" / "qwen3_8b" / args.date.replace("-", "") / "stage5_new_source_versions_999"
    if output_dir.exists() and any(output_dir.iterdir()) and not args.force:
        raise SystemExit(f"OUTPUT_DIR_NOT_EMPTY:{output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    old = historical_sets(ROOT / "runtime" / "training" / "qwen3_8b", before_date=args.date.replace("-", ""))
    evidence = {run_id: run_evidence(run_id) for run_id in EXPECTED_RUNS}
    products = read_products()
    candidate_pool, exclusions = build_candidates(old, evidence, products, read_blocked_skus())
    rows = select_run_quotas(candidate_pool)
    if len(rows) != args.limit:
        raise SystemExit(f"EXPECTED_CANDIDATE_COUNT_999_GOT:{len(rows)}")
    if any(row["prior_pair_seen"] for row in rows):
        raise SystemExit("PRIOR_PAIR_SEEN_MUST_BE_FALSE")

    batch_dir = output_dir / "batches"
    batch_dir.mkdir(parents=True, exist_ok=True)
    batch_counts: dict[str, int] = {}
    batch_artifacts: dict[str, dict[str, Any]] = {}
    for run_id in EXPECTED_RUNS:
        batch_rows = [row for row in rows if row["source_run_id"] == run_id]
        batch_counts[run_id] = len(batch_rows)
        if len(batch_rows) != EXPECTED_BATCH_COUNTS[run_id]:
            raise SystemExit(f"EXPECTED_BATCH_COUNT_MISMATCH:{run_id}:{len(batch_rows)}")
        batch_id = f"stage5-source-version-v2-20260913-{run_id.replace('-', '').replace('_', '-')}"
        path = batch_dir / f"stage5_source_versions_v2_{run_id.replace('-', '').replace('_', '-')}_{len(batch_rows)}_source_only.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            for row in batch_rows:
                handle.write(json.dumps({
                    "messages": [{"role": "user", "content": json.dumps(row["source"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))}],
                    "metadata": metadata_for(row, batch_id),
                }, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        batch_artifacts[run_id] = {"path": str(path.resolve()), "count": len(batch_rows), "sha256": sha256(path)}

    audit_path = output_dir / "stage5_source_versions_v2_999_audit.csv"
    audit_fields = [
        "sku", "source_run_id", "source_hash", "source_hash_contract_version", "source_version_status",
        "prior_pair_seen", "prior_sku_seen", "prior_family_seen", "frozen_test_sku_seen", "frozen_test_family_seen",
        "family_key", "family_key_method", "source_consistency_status", "source_consistency_flags", "candidate_status",
    ]
    with audit_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=audit_fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "sku": row["sku"], "source_run_id": row["source_run_id"], "source_hash": row["source_hash"],
                "source_hash_contract_version": SOURCE_HASH_CONTRACT_VERSION,
                "source_version_status": row["source_version_status"], "prior_pair_seen": row["prior_pair_seen"],
                "prior_sku_seen": row["prior_sku_seen"], "prior_family_seen": row["prior_family_seen"],
                "frozen_test_sku_seen": row["frozen_test_membership"]["sku_seen"],
                "frozen_test_family_seen": row["frozen_test_membership"]["family_seen"],
                "family_key": row["family_key"], "family_key_method": row["family_key_method"],
                "source_consistency_status": row["source_consistency_status"],
                "source_consistency_flags": ",".join(row["source_consistency_flags"]),
                "candidate_status": row["candidate_status"],
            })

    manifest = {
        "contract_id": "STAGE5_SOURCE_CANDIDATE_V2",
        "status": "READY_FOR_HUMAN_GOLD_REVIEW",
        "requested_count": args.limit,
        "selected_count": len(rows),
        "candidate_pool_count": len(candidate_pool),
        "source_runs": list(EXPECTED_RUNS),
        "batch_counts": batch_counts,
        "source_hash_algorithm": SOURCE_HASH_ALGORITHM,
        "source_hash_contract_version": SOURCE_HASH_CONTRACT_VERSION,
        "family_key_method": FAMILY_KEY_METHOD,
        "source_version_counts": dict(sorted(Counter(row["source_version_status"] for row in rows).items())),
        "prior_sku_seen_counts": dict(sorted(Counter(str(row["prior_sku_seen"]).lower() for row in rows).items())),
        "prior_family_seen_counts": dict(sorted(Counter(str(row["prior_family_seen"]).lower() for row in rows).items())),
        "frozen_test_membership_counts": {
            "sku_seen": sum(1 for row in rows if row["frozen_test_membership"]["sku_seen"]),
            "family_seen": sum(1 for row in rows if row["frozen_test_membership"]["family_seen"]),
        },
        "source_consistency_counts": dict(sorted(Counter(row["source_consistency_status"] for row in rows).items())),
        "source_consistency_rules": SOURCE_CONSISTENCY_RULES,
        "candidate_status_counts": dict(sorted(Counter(row["candidate_status"] for row in rows).items())),
        "historical_exact_source_pair_overlap": 0,
        "historical_split_cutoff_exclusive": args.date.replace("-", ""),
        "production_writes": False,
        "training_runs": 0,
        "training_eligible": False,
        "selection_rule": "source-pair novel; trusted NORMAL run; frozen per-run quotas; source-only pending human Gold",
        "excluded_counts": dict(sorted(exclusions.items())),
        "run_evidence": evidence,
        "artifacts": {
            "batches": batch_artifacts,
            "audit_csv": {"path": str(audit_path.resolve()), "sha256": sha256(audit_path)},
        },
        "required_next_step": "Human review, source consistency disposition, Chinese Gold creation, then SKU/family/frozen-test split gates.",
    }
    manifest_path = output_dir / "stage5_source_versions_v2_999_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path = output_dir / "STAGE5_SOURCE_VERSION_CANDIDATE_V2.md"
    consistency_counts = manifest["source_consistency_counts"]
    markdown_path.write_text(
        "# STAGE5 Source-Version Candidate Queue v2\n\n"
        "本目录只包含 source-only 候选，不是 Gold，不是训练集，不写入生产数据。\n\n"
        f"- 记录数：**{len(rows)}**\n"
        f"- Source hash 合同：`{SOURCE_HASH_CONTRACT_VERSION}` / `{SOURCE_HASH_ALGORITHM}`\n"
        f"- 产品族算法：`{FAMILY_KEY_METHOD}`\n"
        f"- Source consistency：`{json.dumps(consistency_counts, ensure_ascii=False)}`\n"
        f"- 生产写入：`{manifest['production_writes']}`；训练运行：`{manifest['training_runs']}`\n\n"
        "## 批次\n\n"
        + "\n".join(f"- `{run_id}`：{count}" for run_id, count in batch_counts.items())
        + "\n\n## 下一步\n\n"
        "人工复核 source consistency 和中文 Gold；之后再执行 Guard、SKU/family/frozen-test 隔离与 train/validation/test split。\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": manifest["status"], "selected_count": len(rows), "batch_counts": batch_counts,
        "source_version_counts": manifest["source_version_counts"],
        "source_consistency_counts": consistency_counts,
        "output_dir": str(output_dir.resolve()),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
