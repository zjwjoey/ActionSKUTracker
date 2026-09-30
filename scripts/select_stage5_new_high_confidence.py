"""Select leakage-safe, high-confidence NEW source rows for Stage 5.

This is deliberately source-only: it never invents a Chinese target and never
writes Master, SQLite, or a dictionary.  A row is NEW when its PRIMARY
``first_seen_at`` is on/after ``--since`` and it is still CURRENT.  Every
historical train/validation/test SKU is excluded, so a requested quota is
reported as unavailable rather than being filled with leaked rows.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from action_tracker.services.hashing import localization_source_hash

FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")
POLLUTION = re.compile(
    r"<[^>]+>|\bnull\.|\bundefined\b|añadir a tus favoritos|加入收藏|[\u3400-\u9fff]",
    re.IGNORECASE,
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jsonl_rows(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def historical_split_skus(training_root: Path) -> set[str]:
    """All SKUs in frozen train/validation/test artifacts (no leakage)."""
    result: set[str] = set()
    for path in training_root.rglob("*.jsonl"):
        if ".venv" in path.parts:
            continue
        if not path.name.endswith(("_train.jsonl", "_validation.jsonl", "_test.jsonl")):
            continue
        for row in jsonl_rows(path):
            sku = str((row.get("metadata") or {}).get("sku") or "").strip()
            if sku:
                result.add(sku)
    return result


def latest_observation(conn: sqlite3.Connection, sku: str) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT run_id, observation_date FROM observations
           WHERE official_sku=? ORDER BY observation_date DESC, run_id DESC LIMIT 1""",
        (sku,),
    ).fetchone()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="2026-08-10", help="Inclusive first_seen_at date")
    parser.add_argument("--date", default="2026-09-13", help="Artifact date YYYY-MM-DD")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args()
    datetime.strptime(args.since, "%Y-%m-%d")
    if args.limit <= 0:
        raise SystemExit("LIMIT_MUST_BE_POSITIVE")

    out_dir = Path(args.output_dir) if args.output_dir else (
        ROOT / "runtime" / "training" / "qwen3_8b" / args.date.replace("-", "") / "stage5_new_high_confidence"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    training_root = ROOT / "runtime" / "training" / "qwen3_8b"
    split_skus = historical_split_skus(training_root)
    blocked_path = ROOT / "runtime" / "dictionary" / "source_damage_report.csv"
    blocked = set()
    if blocked_path.exists():
        with blocked_path.open(encoding="utf-8-sig", newline="") as handle:
            blocked = {
                str(row.get("sku") or "").strip()
                for row in csv.DictReader(handle)
                if str(row.get("status") or "").strip() in {"SOURCE_DAMAGED", "SOURCE_POLLUTED"}
            }

    conn = sqlite3.connect(ROOT / "runtime" / "db" / "action_tracker.db")
    conn.row_factory = sqlite3.Row
    products = conn.execute(
        """SELECT canonical_id, official_sku, first_seen_at, last_seen_at,
                  last_checked_at, source_hash, status
           FROM products WHERE status='CURRENT' AND first_seen_at>=?
           ORDER BY first_seen_at DESC, CAST(official_sku AS INTEGER), official_sku""",
        (args.since,),
    ).fetchall()
    counts = Counter()
    candidates: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for product in products:
        sku = str(product["official_sku"])
        reason = None
        if sku in split_skus:
            reason = "HISTORICAL_SPLIT_OVERLAP"
        elif sku in blocked:
            reason = "SOURCE_DAMAGE_BLOCK"
        else:
            source_row = conn.execute(
                """SELECT name, cat1, cat2, spec, description, details, source_hash
                   FROM product_localizations
                   WHERE official_sku=? AND language='es'""",
                (sku,),
            ).fetchone()
            if source_row is None:
                reason = "MISSING_ES_LOCALIZATION"
            else:
                source = {field: str(source_row[field] or "").strip() for field in FIELDS}
                if any(not source[field] for field in FIELDS):
                    reason = "SOURCE_INCOMPLETE"
                elif any(POLLUTION.search(source[field]) for field in FIELDS):
                    reason = "SOURCE_POLLUTED"
                else:
                    computed = localization_source_hash({
                        "name_es": source["name"], "cat1_es": source["cat1"],
                        "cat2_es": source["cat2"], "spec_es": source["spec"],
                        "desc_es": source["description"], "details_es": source["details"],
                    })
                    if computed != str(product["source_hash"] or ""):
                        reason = "SOURCE_HASH_MISMATCH"
                    else:
                        observation = latest_observation(conn, sku)
                        if observation is None:
                            reason = "MISSING_LATEST_OBSERVATION"
        if reason:
            counts[reason] += 1
            excluded.append({"sku": sku, "reason": reason, "first_seen_at": product["first_seen_at"]})
            continue
        # source_row/source are defined in the non-rejected branch above
        candidates.append({
            "sku": sku,
            "canonical_id": str(product["canonical_id"] or ""),
            "first_seen_at": str(product["first_seen_at"]),
            "last_seen_at": str(product["last_seen_at"] or ""),
            "last_checked_at": str(product["last_checked_at"] or ""),
            "run_id": str(observation["run_id"]),
            "observation_date": str(observation["observation_date"]),
            "source_hash": localization_source_hash({
                "name_es": source["name"], "cat1_es": source["cat1"],
                "cat2_es": source["cat2"], "spec_es": source["spec"],
                "desc_es": source["description"], "details_es": source["details"],
            }),
            "source": source,
            "selection_reasons": ["NEW"],
        })
    conn.close()

    candidates.sort(key=lambda row: (-int(row["first_seen_at"].replace("-", "")), int(row["sku"]) if row["sku"].isdigit() else 10**18, row["sku"]))
    selected = candidates[: args.limit]
    status = "READY_FOR_STAGE5_REVIEW" if len(candidates) >= args.limit else "INSUFFICIENT_NEW_CANDIDATES"
    data_path = out_dir / f"stage5_new_high_confidence_{len(selected)}_source_only.jsonl"
    audit_path = out_dir / f"stage5_new_high_confidence_{len(selected)}_audit.csv"
    with data_path.open("w", encoding="utf-8") as handle:
        for row in selected:
            handle.write(json.dumps({
                "messages": [
                    {"role": "user", "content": json.dumps(row["source"], ensure_ascii=False, sort_keys=True)},
                ],
                "metadata": {
                    "batch_id": f"stage5-new-{args.date.replace('-', '')}",
                    "run_id": row["run_id"], "observation_date": row["observation_date"],
                    "sku": row["sku"], "canonical_id": row["canonical_id"],
                    "source_hash": row["source_hash"], "selection_reasons": ["NEW"],
                    "candidate_status": "SOURCE_ONLY_AWAITING_LABEL_REVIEW",
                    "confidence_status": "HIGH_CONFIDENCE_SOURCE_INTEGRITY",
                },
            }, ensure_ascii=False, sort_keys=True) + "\n")
    with audit_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sku", "first_seen_at", "observation_date", "source_hash", "selection_reasons", "confidence_status"])
        writer.writeheader()
        for row in selected:
            writer.writerow({"sku": row["sku"], "first_seen_at": row["first_seen_at"], "observation_date": row["observation_date"], "source_hash": row["source_hash"], "selection_reasons": "NEW", "confidence_status": "HIGH_CONFIDENCE_SOURCE_INTEGRITY"})
    excluded_path = out_dir / "stage5_new_high_confidence_excluded.csv"
    with excluded_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sku", "first_seen_at", "reason"])
        writer.writeheader(); writer.writerows(excluded)
    manifest = {
        "status": status, "requested_count": args.limit, "selected_count": len(selected),
        "candidate_pool_count": len(candidates), "since": args.since, "date": args.date,
        "selection_definition": "CURRENT + first_seen_at >= since + complete/pollution-free ES six fields + source_hash match + latest observation + no historical train/validation/test overlap",
        "historical_split_sku_count": len(split_skus),
        "historical_split_overlap_in_recent_pool": counts["HISTORICAL_SPLIT_OVERLAP"],
        "excluded_counts": dict(sorted(counts.items())),
        "training_eligible": False,
        "production_writes": False,
        "artifacts": {
            "source_only_jsonl": str(data_path), "source_only_jsonl_sha256": sha256(data_path),
            "audit_csv": str(audit_path), "audit_csv_sha256": sha256(audit_path),
            "excluded_csv": str(excluded_path), "excluded_csv_sha256": sha256(excluded_path),
        },
        "required_next_step": "Human-review every selected row; only approved rows may be split into a new train/validation/test set.",
    }
    manifest_path = out_dir / "stage5_new_high_confidence_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
