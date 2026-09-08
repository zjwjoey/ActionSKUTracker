import csv
import hashlib
import json
import sqlite3
from pathlib import Path

from action_tracker.localization.feed import build_knowledge_feed, dictionary_hashes


def _write_csv(path: Path, headers: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def _evidence(*skus: str, source_hash: str = "h") -> str:
    return json.dumps([{"sku": sku, "source_hash": f"{source_hash}{sku}", "source_run_id": "run", "source_commit_id": "commit", "source_example": "example"} for sku in skus], ensure_ascii=False)


def _snapshot(path: Path) -> None:
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE products (canonical_id TEXT, official_sku TEXT, status TEXT);
        CREATE TABLE commit_batches (commit_id TEXT, status TEXT, committed_at TEXT);
        INSERT INTO products VALUES ('ACT1','1','CURRENT');
        INSERT INTO products VALUES ('ACT2','2','CURRENT');
        INSERT INTO products VALUES ('ACT3','3','CURRENT');
        INSERT INTO products VALUES ('ACT9','9','HISTORICAL');
        INSERT INTO commit_batches VALUES ('source-commit','COMMITTED','2026-09-01T00:00:00Z');
        """
    )
    db.close()


def _inputs(tmp_path: Path):
    audit = tmp_path / "audit.csv"
    _write_csv(
        audit,
        ["sku", "source_hash", "readiness", "review_reasons", "source_run_id", "source_commit_id"],
        [
            {"sku": "1", "source_hash": "h1", "readiness": "REVIEW_REQUIRED", "review_reasons": "PRODUCT_TYPE_REVIEW", "source_run_id": "run", "source_commit_id": "commit"},
            {"sku": "2", "source_hash": "h2", "readiness": "REVIEW_REQUIRED", "review_reasons": "TECH_TOKEN_REVIEW", "source_run_id": "run", "source_commit_id": "commit"},
            {"sku": "3", "source_hash": "h3", "readiness": "READY", "review_reasons": "", "source_run_id": "run", "source_commit_id": "commit"},
        ],
    )
    learning = tmp_path / "learning.csv"
    _write_csv(
        learning,
        ["semantic_type", "source_term", "zh_value", "evidence_json", "source_hash"],
        [
            {"semantic_type": "PRODUCT_TYPE", "source_term": "Goma", "zh_value": "橡皮擦", "evidence_json": _evidence("1", "2"), "source_hash": ""},
            {"semantic_type": "PRODUCT_TYPE", "source_term": "Goma", "zh_value": "橡皮", "evidence_json": _evidence("3"), "source_hash": ""},
            {"semantic_type": "TECH_TOKEN", "source_term": "USB-C", "zh_value": "USB-C", "evidence_json": _evidence("2"), "source_hash": ""},
            {"semantic_type": "STANDARD_UNIT", "source_term": "gramos", "zh_value": "g", "evidence_json": _evidence("1"), "source_hash": ""},
        ],
    )
    review = tmp_path / "review.csv"
    _write_csv(review, ["sku", "issue_type"], [{"sku": "1", "issue_type": "PRODUCT_TYPE_REVIEW"}, {"sku": "2", "issue_type": "TECH_TOKEN_REVIEW"}])
    return audit, learning, review


def test_feed_aggregates_evidence_conflict_and_priority_without_ai(tmp_path):
    snapshot = tmp_path / "snapshot.db"
    _snapshot(snapshot)
    audit, learning, review = _inputs(tmp_path)
    dictionary = tmp_path / "dictionary"
    dictionary.mkdir()
    output = tmp_path / "output"
    before = dictionary_hashes(dictionary)
    summary = build_knowledge_feed(
        audit_csv=audit,
        learning_csv=learning,
        review_csv=review,
        snapshot=snapshot,
        dictionary_dir=dictionary,
        output_dir=output,
        run_id="feed-test",
        source_commit_id="source-commit",
    )
    rows = list(csv.DictReader((output / "knowledge_feed_candidates.csv").open(encoding="utf-8-sig", newline="")))
    assert summary["AI_calls"] == 0
    assert summary["PRODUCT_TYPE"] == 1
    assert summary["TECH_TOKEN"] == 1
    assert rows[0]["knowledge_type"] == "PRODUCT_TYPE"
    assert rows[0]["status"] == "EVIDENCE_CONFLICT"
    assert rows[0]["affected_sku_count"] == "3"
    assert json.loads(rows[0]["evidence_json"])[0]["source_hash"]
    assert summary["dictionary_unchanged"] is True
    assert dictionary_hashes(dictionary) == before
    assert (output / "knowledge_feed_top_200.csv").exists()
    assert (output / "knowledge_feed_impact.csv").exists()


def test_existing_knowledge_is_excluded_from_review_pool(tmp_path):
    snapshot = tmp_path / "snapshot.db"
    _snapshot(snapshot)
    audit, learning, review = _inputs(tmp_path)
    dictionary = tmp_path / "dictionary"
    dictionary.mkdir()
    _write_csv(
        dictionary / "tech_token_dictionary.csv",
        ["schema_version", "token", "canonical_token", "token_type", "keep_original", "normalization_rule", "review_status", "notes"],
        [{"schema_version": "1.0", "token": "USB-C", "canonical_token": "USB-C", "token_type": "INTERFACE", "keep_original": "true", "normalization_rule": "", "review_status": "LOCKED", "notes": ""}],
    )
    output = tmp_path / "output"
    summary = build_knowledge_feed(audit_csv=audit, learning_csv=learning, review_csv=review, snapshot=snapshot, dictionary_dir=dictionary, output_dir=output, run_id="feed-test")
    rows = list(csv.DictReader((output / "knowledge_feed_candidates.csv").open(encoding="utf-8-sig", newline="")))
    usb = next(row for row in rows if row["source_term"] == "USB-C")
    assert usb["status"] == "EXISTING_KNOWLEDGE"
    top = list(csv.DictReader((output / "knowledge_feed_top_200.csv").open(encoding="utf-8-sig", newline="")))
    assert all(row["status"] != "EXISTING_KNOWLEDGE" for row in top)
    assert top[0]["source_term"] == "Goma"
    assert summary["existing_knowledge_skipped"] == 1
