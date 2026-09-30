"""Select 1,000 fresh source snapshots for Stage 5 without train leakage.

The unit of novelty is ``(official_sku, localization_source_hash)``.  This
allows a genuinely new official source version of an existing SKU to enter the
queue as SOURCE_HASH_CHANGED, while an exact source record already present in
any frozen train/validation/test split is excluded.  The output is source-only
and satisfies the Stage 5 input contract; it contains no assistant labels.
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
from datetime import date
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from action_tracker.services.hashing import localization_source_hash

FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")
RAW_KEYS = {
    "name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es", "spec": "spec_es",
    "description": "desc_es", "details": "details_es",
}
POLLUTION = re.compile(
    r"<[^>]+>|\bnull\.|\bundefined\b|añadir a tus favoritos|加入收藏|[\u3400-\u9fff]",
    re.IGNORECASE,
)
ARTICLE_NO = re.compile(r"n[uú]mero\s+del\s+art[ií]culo\s*[:：]\s*(\d+)", re.IGNORECASE)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jsonl_rows(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def historical_pairs(training_root: Path) -> tuple[set[tuple[str, str]], set[str]]:
    pairs: set[tuple[str, str]] = set()
    skus: set[str] = set()
    for path in training_root.rglob("*.jsonl"):
        if ".venv" in path.parts or not path.name.endswith(("_train.jsonl", "_validation.jsonl", "_test.jsonl")):
            continue
        for row in jsonl_rows(path):
            metadata = row.get("metadata") or {}
            sku = str(metadata.get("sku") or "").strip()
            source_hash = str(metadata.get("source_hash") or "").strip().lower()
            if sku and source_hash:
                pairs.add((sku, source_hash))
                skus.add(sku)
    return pairs, skus


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="2026-09-13")
    parser.add_argument("--since-run", default="2026-09-09_032720")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args()
    date.fromisoformat(args.date)
    if args.limit <= 0:
        raise SystemExit("LIMIT_MUST_BE_POSITIVE")
    out_dir = Path(args.output_dir) if args.output_dir else ROOT / "runtime" / "training" / "qwen3_8b" / args.date.replace("-", "") / "stage5_new_source_snapshots"
    out_dir.mkdir(parents=True, exist_ok=True)

    old_pairs, old_skus = historical_pairs(ROOT / "runtime" / "training" / "qwen3_8b")
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
    snapshot_files = sorted(
        (path for path in (ROOT / "runtime" / "snapshots").glob("*/*/products_normalized.csv")
         if path.parent.name >= args.since_run),
        key=lambda path: path.parent.name,
    )
    runs = [path.parent.name for path in snapshot_files]
    # Product-fact snapshots are a fallback for runs that do not have the
    # normalized CSV artifact.  The current repository has both sources for
    # 2026-09-09 onward, so the CSV is preferred as the official evidence.
    if not runs:
        runs = [str(row[0]) for row in conn.execute(
            "SELECT DISTINCT run_id FROM product_fact_versions WHERE run_id>=? ORDER BY run_id",
            (args.since_run,),
        )]
    if not runs:
        raise SystemExit("NO_SOURCE_SNAPSHOT_RUNS")
    products = {
        str(row["official_sku"]): row
        for row in conn.execute("SELECT * FROM products")
    }
    counts = Counter()
    candidates: dict[tuple[str, str], dict[str, Any]] = {}
    for run_id in runs:
        csv_path = ROOT / "runtime" / "snapshots" / run_id[:10] / run_id / "products_normalized.csv"
        if csv_path.exists():
            with csv_path.open(encoding="utf-8-sig", newline="") as handle:
                snapshot_rows = list(csv.DictReader(handle))
        else:
            snapshot_rows = []
            for fact in conn.execute("SELECT * FROM product_fact_versions WHERE run_id=?", (run_id,)):
                try:
                    raw = json.loads(str(fact["raw_fact_json"] or ""))
                except json.JSONDecodeError:
                    counts["RAW_JSON_INVALID"] += 1
                    continue
                snapshot_rows.append({
                    "sku": str(fact["official_sku"]), "canonical_id": f"ACT{fact['official_sku']}",
                    "first_seen": "", "name_es": raw.get("name_es"), "cat1_es": raw.get("cat1_es"),
                    "cat2_es": raw.get("cat2_es"), "spec_es": raw.get("spec_es"),
                    "desc_es": raw.get("desc_es"), "details_es": raw.get("details_es"),
                })
        for snapshot_row in snapshot_rows:
            sku = str(snapshot_row.get("sku") or "").strip()
            if not sku:
                counts["SKU_EMPTY"] += 1
                continue
            if sku in blocked:
                counts["SOURCE_DAMAGE_BLOCK"] += 1
                continue
            source = {
                field: str(snapshot_row.get(RAW_KEYS[field]) or "").strip()
                for field in FIELDS
            }
            if any(not source[field] for field in FIELDS):
                counts["SOURCE_INCOMPLETE"] += 1
                continue
            if any(POLLUTION.search(source[field]) for field in FIELDS):
                counts["SOURCE_POLLUTED"] += 1
                continue
            article = ARTICLE_NO.search(source["details"])
            if article and article.group(1) != sku:
                counts["DETAIL_SKU_MISMATCH"] += 1
                continue
            source_hash = localization_source_hash({
                "name_es": source["name"], "cat1_es": source["cat1"],
                "cat2_es": source["cat2"], "spec_es": source["spec"],
                "desc_es": source["description"], "details_es": source["details"],
            })
            key = (sku, source_hash)
            if key in old_pairs:
                counts["HISTORICAL_EXACT_SOURCE_OVERLAP"] += 1
                continue
            product = products.get(sku)
            canonical_id = str(snapshot_row.get("canonical_id") or (product["canonical_id"] if product else f"ACT{sku}"))
            first_seen = str(product["first_seen_at"] if product else snapshot_row.get("first_seen") or "")
            observation_date = run_id[:10]
            reason = "NEW" if first_seen == observation_date or sku not in old_skus else "SOURCE_HASH_CHANGED"
            candidate = {
                "sku": sku, "canonical_id": canonical_id, "run_id": run_id,
                "observation_date": observation_date, "first_seen_at": first_seen,
                "source_hash": source_hash, "source": source,
                "selection_reasons": [reason],
                "raw_fact_hash": str(snapshot_row.get("source_hash") or ""),
            }
            # If the same source version appears in multiple snapshots, keep the
            # newest run deterministically.
            previous = candidates.get(key)
            if previous is None or candidate["run_id"] > previous["run_id"]:
                candidates[key] = candidate

    conn.close()
    rows = list(candidates.values())
    rows.sort(key=lambda row: (
        0 if row["selection_reasons"] == ["NEW"] else 1,
        -int(row["run_id"].replace("-", "").replace("_", "")),
        int(row["sku"]) if row["sku"].isdigit() else 10**18,
        row["source_hash"],
    ))
    selected = rows[: args.limit]
    status = "READY_FOR_STAGE5_REVIEW" if len(rows) >= args.limit else "INSUFFICIENT_FRESH_SOURCE_SNAPSHOTS"
    batch_dir = out_dir / "batches"
    batch_dir.mkdir(parents=True, exist_ok=True)
    audit_path = out_dir / f"stage5_new_source_snapshots_{len(selected)}_audit.csv"
    batch_paths: dict[str, Path] = {}
    batch_counts: dict[str, int] = {}
    for run_id in sorted({row["run_id"] for row in selected}):
        batch_rows = [row for row in selected if row["run_id"] == run_id]
        batch_path = batch_dir / f"stage5_new_source_snapshots_{run_id.replace('-', '').replace('_', '-')}_{len(batch_rows)}_source_only.jsonl"
        batch_paths[run_id] = batch_path
        batch_counts[run_id] = len(batch_rows)
        with batch_path.open("w", encoding="utf-8") as handle:
            for row in batch_rows:
                handle.write(json.dumps({
                    "messages": [{"role": "user", "content": json.dumps(row["source"], ensure_ascii=False, sort_keys=True)}],
                    "metadata": {
                        "batch_id": f"stage5-new-snapshots-{args.date.replace('-', '')}-{run_id.replace('-', '').replace('_', '-')}",
                        "run_id": row["run_id"], "observation_date": row["observation_date"],
                        "sku": row["sku"], "canonical_id": row["canonical_id"],
                        "source_hash": row["source_hash"], "selection_reasons": row["selection_reasons"],
                        "candidate_status": "SOURCE_ONLY_AWAITING_LABEL_REVIEW",
                        "confidence_status": "HIGH_CONFIDENCE_SOURCE_SNAPSHOT",
                        "raw_fact_hash": row["raw_fact_hash"],
                    },
                }, ensure_ascii=False, sort_keys=True) + "\n")
    with audit_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sku", "run_id", "observation_date", "first_seen_at", "source_hash", "selection_reasons", "confidence_status"])
        writer.writeheader()
        for row in selected:
            writer.writerow({
                "sku": row["sku"], "run_id": row["run_id"], "observation_date": row["observation_date"],
                "first_seen_at": row["first_seen_at"], "source_hash": row["source_hash"],
                "selection_reasons": ",".join(row["selection_reasons"]), "confidence_status": "HIGH_CONFIDENCE_SOURCE_SNAPSHOT",
            })
    manifest = {
        "status": status, "requested_count": args.limit, "selected_count": len(selected),
        "candidate_pool_count": len(rows), "source_runs": runs, "since_run": args.since_run,
        "novelty_definition": "(official_sku, localization_source_hash) absent from every historical train/validation/test split; NEW or SOURCE_HASH_CHANGED only",
        "historical_train_exact_pair_overlap_selected": 0,
        "old_training_sku_count": len(old_skus), "old_training_exact_pair_count": len(old_pairs),
        "reason_counts_selected": dict(sorted(Counter(r["selection_reasons"][0] for r in selected).items())),
        "reason_counts_pool": dict(sorted(Counter(r["selection_reasons"][0] for r in rows).items())),
        "excluded_counts": dict(sorted(counts.items())),
        "training_eligible": False,
        "production_writes": False,
        "batch_counts": batch_counts,
        "artifacts": {
            "batch_source_only_jsonl": {
                run_id: {"path": str(path), "sha256": sha256(path), "count": batch_counts[run_id]}
                for run_id, path in sorted(batch_paths.items())
            },
            "audit_csv": str(audit_path), "audit_csv_sha256": sha256(audit_path),
        },
        "required_next_step": "Human-review every selected source snapshot; only approved rows may be split into a new train/validation/test set.",
    }
    manifest_path = out_dir / "stage5_new_source_snapshots_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
