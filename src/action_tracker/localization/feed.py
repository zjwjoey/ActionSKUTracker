"""Read-only Localization Knowledge Feed V1.

The feed turns existing audit/learning evidence into reviewable, reusable
knowledge candidates.  It deliberately has no promotion or production-write
path: all inputs are a SQLite snapshot and report files, and the formal
dictionaries are hashed before and after the build.
"""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .knowledge import ACCEPTED_KNOWLEDGE_STATUSES, KnowledgeLoader
from .service import audit_current


FEED_TYPES = ("PRODUCT_TYPE", "PHRASE", "TERM", "DETAIL_KEY", "TECH_TOKEN")
TYPE_PRIORITY = {name: index for index, name in enumerate(FEED_TYPES)}
DICT_FILES = (
    "product_type_dictionary.csv",
    "phrase_dictionary.csv",
    "term_dictionary.csv",
    "detail_key_dictionary.csv",
    "tech_token_dictionary.csv",
    "manual_overrides.csv",
    "product_dictionary.csv",
)


def _normalize(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dictionary_hashes(directory: Path) -> dict[str, str | None]:
    directory = Path(directory)
    return {name: _sha(directory / name) if (directory / name).exists() else None for name in DICT_FILES}


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path or not Path(path).exists():
        return []
    csv.field_size_limit(20_000_000)
    with Path(path).open(encoding="utf-8-sig", newline="") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def _evidence(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = row.get("evidence_json")
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [dict(item) for item in parsed if isinstance(item, Mapping)]
        except (TypeError, json.JSONDecodeError):
            pass
    sku = str(row.get("sku") or "").strip()
    if not sku:
        return []
    return [{
        "sku": sku,
        "source_hash": str(row.get("source_hash") or ""),
        "source_run_id": str(row.get("source_run_id") or ""),
        "source_commit_id": str(row.get("source_commit_id") or ""),
        "source_example": str(row.get("source_example") or row.get("source_term") or ""),
    }]


def _snapshot_current(snapshot: Path) -> tuple[set[str], str | None, dict[str, dict[str, Any]]]:
    """Read only the snapshot and return CURRENT identities/provenance."""
    snapshot = Path(snapshot)
    if not snapshot.exists():
        raise FileNotFoundError(snapshot)
    db = sqlite3.connect(f"file:{snapshot.resolve().as_posix()}?mode=ro", uri=True)
    try:
        tables = {str(row[0]) for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        current: dict[str, dict[str, Any]] = {}
        if "products" in tables:
            try:
                columns = [str(row[1]) for row in db.execute("PRAGMA table_info(products)")]
                rows = db.execute("SELECT * FROM products WHERE status='CURRENT'").fetchall()
                for row in rows:
                    data = dict(zip(columns, row))
                    sku = str(data.get("official_sku") or data.get("sku") or "").strip()
                    if sku:
                        current[sku] = data
            except sqlite3.DatabaseError:
                current = {}
        if "product_localizations" in tables and current:
            try:
                rows = db.execute("SELECT official_sku,cat1,cat2 FROM product_localizations WHERE language='es'").fetchall()
                for sku, cat1, cat2 in rows:
                    key = str(sku or "").strip()
                    if key in current:
                        current[key]["_cat1_es"] = str(cat1 or "")
                        current[key]["_cat2_es"] = str(cat2 or "")
            except sqlite3.DatabaseError:
                pass
        commit_id = None
        if "commit_batches" in tables:
            row = db.execute("SELECT commit_id FROM commit_batches WHERE status='COMMITTED' ORDER BY committed_at DESC LIMIT 1").fetchone()
            commit_id = str(row[0]) if row else None
        return set(current), commit_id, current
    finally:
        db.close()


def snapshot_manifest(snapshot: Path, *, source_commit_id: str | None = None) -> dict[str, Any]:
    snapshot = Path(snapshot)
    current, discovered_commit, _ = _snapshot_current(snapshot)
    return {
        "snapshot": str(snapshot),
        "mode": "SQLITE_BACKUP_READ_ONLY",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "file_size": snapshot.stat().st_size,
        "sha256": _sha(snapshot),
        "current_count": len(current),
        "source_commit_id": source_commit_id or discovered_commit,
    }


def _existing_knowledge(directory: Path) -> set[tuple[str, str, str]]:
    loaded = KnowledgeLoader(directory).load()
    result: set[tuple[str, str, str]] = set()
    product_types = loaded.get("product_types") or {}
    if isinstance(product_types, Mapping):
        for source, value in product_types.items():
            result.add(("PRODUCT_TYPE", _normalize(source), _normalize(value)))
    else:
        for row in product_types:
            result.add(("PRODUCT_TYPE", _normalize(row.get("source_term")), _normalize(row.get("canonical_zh"))))
    for row in loaded.get("phrases") or []:
        result.add(("PHRASE", _normalize(row.get("source_phrase")), _normalize(row.get("zh_value"))))
    for row in loaded.get("detail_keys") or []:
        result.add(("DETAIL_KEY", _normalize(row.get("key_es")), _normalize(row.get("key_zh"))))
    tech_tokens = loaded.get("tech_tokens") or {}
    if isinstance(tech_tokens, Mapping):
        for source, value in tech_tokens.items():
            result.add(("TECH_TOKEN", _normalize(source), _normalize(value)))
    else:
        for row in tech_tokens:
            result.add(("TECH_TOKEN", _normalize(row.get("token")), _normalize(row.get("canonical_token"))))
    for row in loaded.get("terms") or []:
        result.add(("TERM", _normalize(row.get("source_term") or row.get("term_es")), _normalize(row.get("zh_value") or row.get("term_zh"))))
    return {item for item in result if item[1]}


def _manual_pairs(directory: Path) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for row in _read_csv(Path(directory) / "manual_overrides.csv"):
        source = row.get("source_term") or row.get("source_value") or row.get("key_es") or row.get("key") or ""
        value = row.get("value") or row.get("zh_value") or row.get("decision_value") or ""
        if source and value:
            pairs.add((_normalize(source), _normalize(value)))
    return pairs


def build_knowledge_feed(
    *,
    audit_csv: Path,
    learning_csv: Path,
    review_csv: Path | None,
    snapshot: Path,
    dictionary_dir: Path,
    output_dir: Path,
    run_id: str,
    source_commit_id: str | None = None,
) -> dict[str, Any]:
    """Build candidate, impact and summary artifacts without any writes outside output."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    dictionary_dir = Path(dictionary_dir)
    before = dictionary_hashes(dictionary_dir)
    current_skus, discovered_commit, current_rows = _snapshot_current(Path(snapshot))
    source_commit_id = source_commit_id or discovered_commit
    audit_rows = _read_csv(Path(audit_csv))
    learning_rows = _read_csv(Path(learning_csv))
    review_rows = _read_csv(Path(review_csv)) if review_csv else []
    source_by_sku = {str(row.get("sku") or "").strip(): row for row in audit_rows if str(row.get("sku") or "").strip()}
    review_by_sku: dict[str, set[str]] = defaultdict(set)
    for row in review_rows:
        sku = str(row.get("sku") or "").strip()
        if sku and row.get("issue_type"):
            review_by_sku[sku].add(str(row["issue_type"]))
    existing = _existing_knowledge(dictionary_dir)
    manual_pairs = _manual_pairs(dictionary_dir)
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    proposals: dict[tuple[str, str], set[str]] = defaultdict(set)
    evidence_by_key: dict[tuple[str, str], dict[tuple[str, str], dict[str, Any]]] = defaultdict(dict)
    for row in learning_rows:
        kind = str(row.get("semantic_type") or row.get("knowledge_type") or "").strip().upper()
        if kind not in FEED_TYPES:
            continue
        source = str(row.get("source_term") or row.get("normalized_source") or "").strip()
        proposed = str(row.get("zh_value") or row.get("semantic_value") or row.get("canonical_zh") or "").strip()
        normalized = _normalize(source)
        if not normalized or not proposed:
            continue
        key = (kind, normalized)
        proposals[key].add(proposed)
        item = grouped.setdefault(key, {"knowledge_type": kind, "source_term": source, "normalized_source": normalized})
        for evidence in _evidence(row):
            sku = str(evidence.get("sku") or "").strip()
            if not sku or sku not in current_skus:
                continue
            audit = source_by_sku.get(sku, {})
            reasons = set(filter(None, str(audit.get("review_reasons") or "").split("|")))
            if {"SOURCE_BLOCKED", "SOURCE_HASH_MISMATCH", "SOURCE_HASH_CHANGED"} & reasons:
                continue
            evidence_hash = str(evidence.get("source_hash") or "").strip()
            current_hash = str(audit.get("source_hash") or "").strip()
            if evidence_hash and current_hash and evidence_hash != current_hash:
                continue
            evidence = {**evidence, "sku": sku, "source_hash": evidence_hash or current_hash,
                        "source_run_id": str(evidence.get("source_run_id") or audit.get("source_run_id") or ""),
                        "source_commit_id": str(evidence.get("source_commit_id") or audit.get("source_commit_id") or source_commit_id or ""),
                        "source_example": str(evidence.get("source_example") or source) }
            evidence_by_key[key][(sku, str(evidence.get("source_hash") or ""))] = evidence
    candidates: list[dict[str, Any]] = []
    for key, item in grouped.items():
        kind, normalized = key
        evidence = sorted(evidence_by_key.get(key, {}).values(), key=lambda value: (str(value.get("sku")), str(value.get("source_hash"))))
        if not evidence:
            continue
        values = sorted(proposals[key])
        proposed = values[0] if len(values) == 1 else ""
        evidence_skus = sorted({str(value.get("sku")) for value in evidence})
        contexts = sorted({
            "｜".join(part for part in (str(current_rows.get(sku, {}).get("_cat1_es") or ""), str(current_rows.get(sku, {}).get("_cat2_es") or "")) if part)
            for sku in evidence_skus
            if any(str(current_rows.get(sku, {}).get(part) or "").strip() for part in ("_cat1_es", "_cat2_es"))
        })
        same_existing = any((kind, normalized, _normalize(value)) in existing for value in values)
        manual_conflict = any(source == normalized and target != _normalize(proposed) for source, target in manual_pairs) if proposed else False
        evidence_conflict = len(values) > 1
        if same_existing:
            status = "EXISTING_KNOWLEDGE"
        elif evidence_conflict:
            status = "EVIDENCE_CONFLICT"
        elif manual_conflict:
            status = "MANUAL_CONFLICT"
        else:
            status = "EVIDENCE_ACCUMULATED" if len(evidence_skus) > 1 else "PENDING"
        candidate_id = hashlib.sha256(json.dumps([kind, normalized], ensure_ascii=False).encode("utf-8")).hexdigest()
        reasons = sorted({reason for sku in evidence_skus for reason in review_by_sku.get(sku, set())})
        candidates.append({
            "candidate_id": candidate_id,
            "knowledge_type": kind,
            "source_term": item["source_term"],
            "normalized_source": normalized,
            "proposed_zh": proposed,
            "category_context": "｜".join(contexts),
            "affected_sku_count": len(evidence_skus),
            "evidence_sku_count": len(evidence_skus),
            "review_reason": "|".join(reasons) or status,
            "status": status,
            "existing_knowledge": str(bool(same_existing)).lower(),
            "manual_conflict": str(bool(manual_conflict)).lower(),
            "evidence_conflict": str(bool(evidence_conflict)).lower(),
            "needs_ai": str(status not in {"EXISTING_KNOWLEDGE", "MANUAL_CONFLICT", "EVIDENCE_CONFLICT"}).lower(),
            "priority_rank": 0,
            "evidence_json": json.dumps(evidence, ensure_ascii=False, sort_keys=True),
        })
    candidates.sort(key=lambda row: (-int(row["affected_sku_count"]), -int(row["evidence_sku_count"]), TYPE_PRIORITY[row["knowledge_type"]], row["normalized_source"]))
    # Existing formal knowledge is retained in the complete evidence file for
    # traceability, but must not consume a human-review ranking slot.
    review_candidates = [row for row in candidates if row["status"] != "EXISTING_KNOWLEDGE"]
    for index, row in enumerate(review_candidates, 1):
        row["priority_rank"] = index
    for row in candidates:
        if row["status"] == "EXISTING_KNOWLEDGE":
            row["priority_rank"] = ""
    headers = ["candidate_id", "knowledge_type", "source_term", "normalized_source", "proposed_zh", "category_context", "affected_sku_count", "evidence_sku_count", "review_reason", "status", "existing_knowledge", "manual_conflict", "evidence_conflict", "needs_ai", "priority_rank", "evidence_json"]
    def write_csv(path: Path, rows: Iterable[Mapping[str, Any]], fields: list[str]) -> None:
        with path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields); writer.writeheader(); writer.writerows({field: row.get(field, "") for field in fields} for row in rows)
    write_csv(output_dir / "knowledge_feed_candidates.csv", candidates, headers)
    write_csv(output_dir / "knowledge_feed_top_200.csv", review_candidates[:200], headers)
    impact_rows = [{"candidate_id": row["candidate_id"], "knowledge_type": row["knowledge_type"], "source_term": row["source_term"], "affected_sku_count": row["affected_sku_count"], "affected_skus": "|".join(sorted({str(item.get("sku")) for item in json.loads(row["evidence_json"])}))} for row in candidates]
    write_csv(output_dir / "knowledge_feed_impact.csv", impact_rows, ["candidate_id", "knowledge_type", "source_term", "affected_sku_count", "affected_skus"])
    after = dictionary_hashes(dictionary_dir)
    if before != after:
        raise RuntimeError("FORMAL_DICTIONARY_CHANGED_DURING_FEED")
    counts = Counter(row["knowledge_type"] for row in candidates)
    summary = {
        "run_id": run_id,
        "snapshot": snapshot_manifest(Path(snapshot), source_commit_id=source_commit_id),
        "CURRENT": len(current_skus),
        "REVIEW_REQUIRED": sum(1 for row in audit_rows if str(row.get("readiness") or "") == "REVIEW_REQUIRED"),
        "candidate_count": len(candidates),
        **{name: counts.get(name, 0) for name in FEED_TYPES},
        "existing_knowledge_skipped": sum(row["status"] == "EXISTING_KNOWLEDGE" for row in candidates),
        "manual_conflicts": sum(row["status"] == "MANUAL_CONFLICT" for row in candidates),
        "evidence_conflicts": sum(row["status"] == "EVIDENCE_CONFLICT" for row in candidates),
        "needs_ai": sum(row["needs_ai"] == "true" for row in candidates),
        "review_candidate_count": len(review_candidates),
        "top_10_candidates": [{"rank": row["priority_rank"], "candidate_id": row["candidate_id"], "knowledge_type": row["knowledge_type"], "source_term": row["source_term"], "proposed_zh": row["proposed_zh"], "affected_sku_count": row["affected_sku_count"]} for row in review_candidates[:10]],
        "estimated_impacted_skus": len({
            str(evidence.get("sku"))
            for row in candidates
            for evidence in json.loads(row["evidence_json"])
            if str(evidence.get("sku") or "").strip()
        }),
        "AI_calls": 0,
        "dictionary_hashes_before": before,
        "dictionary_hashes_after": after,
        "dictionary_unchanged": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (output_dir / "knowledge_feed_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def run_feed(cfg: Mapping[str, Any], *, snapshot: Path, output_dir: Path, run_id: str = "knowledge-feed-v1-baseline") -> dict[str, Any]:
    """Run a snapshot-only audit followed by feed generation."""
    snapshot = Path(snapshot); output_dir = Path(output_dir)
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    provenance = snapshot_manifest(snapshot)
    (snapshot.parent / "snapshot_manifest.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8")
    feed_cfg = dict(cfg)
    feed_cfg["storage"] = {**dict(cfg.get("storage") or {}), "db_path": str(snapshot)}
    feed_cfg["paths"] = {**dict(cfg.get("paths") or {})}
    feed_cfg["paths"]["temp"] = output_dir.parent / "audit_tmp"
    feed_cfg["paths"]["dictionary_baseline"] = Path(cfg["project_root"]) / "data" / "dictionary"
    feed_cfg["localization"] = {**dict(cfg.get("localization") or {}), "ai": {**dict((cfg.get("localization") or {}).get("ai") or {}), "enabled": False}}
    result = audit_current(feed_cfg, run_id=run_id, persist_reviews=False)
    report_dir = Path(result["report_dir"])
    report_copy = output_dir.parent / "reports" / run_id
    report_copy.parent.mkdir(parents=True, exist_ok=True)
    if report_copy.exists():
        shutil.rmtree(report_copy)
    shutil.copytree(report_dir, report_copy)
    summary = build_knowledge_feed(
        audit_csv=report_copy / "localization_audit.csv",
        learning_csv=report_copy / "learning_candidates.csv",
        review_csv=report_copy / "review_queue.csv",
        snapshot=snapshot,
        dictionary_dir=feed_cfg["paths"]["dictionary_baseline"],
        output_dir=output_dir,
        run_id=run_id,
        source_commit_id=result.get("source_commit_id"),
    )
    summary["audit_report"] = str(report_copy)
    (output_dir / "knowledge_feed_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
