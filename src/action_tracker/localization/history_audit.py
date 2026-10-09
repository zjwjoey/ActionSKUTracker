"""Read-only, field-scoped historical localization evidence inventory.

This module deliberately does not turn a missing historical field into an
official empty source, and never promotes mechanical QA to semantic approval.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse

from .contracts import CANONICAL_TO_SOURCE, CANONICAL_TO_ZH, SourceFacts
from .qa import guard_translation
from ..database.repository import ProductionRepository
from ..services.hashing import localization_field_source_hash

FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")


def verified_archive_identity(row, skus):
    sku = str(row.get("编号") or "").strip()
    url = urlparse(str(row.get("商品链接") or ""))
    return (sku in skus and sku.isdigit()
            and row.get("核验结论") in {"历史官方资料已确认", "当前官网已确认"}
            and url.scheme == "https" and url.hostname in {"action.com", "www.action.com"}
            and bool(re.fullmatch(r"/es-es/p/" + re.escape(sku) + r"(?:/.*)?", url.path)))


def write_detail_reports(rows, output):
    output = Path(output)
    groups = {
        "source_unavailable": [r for r in rows if r["action"] == "RESTORE_SOURCE"],
        "source_conflicts": [r for r in rows if r["action"] in {"REVIEW_SOURCE_VERSION", "REVIEW_SOURCE_LANGUAGE"}],
        "stale": [r for r in rows if r["freshness"] == "STALE"],
        "qa_review": [r for r in rows if r["qa_findings"]],
        "recoverable_chinese": [r for r in rows if r["recoverable_chinese"] and not r["chinese"]],
    }
    chosen = []; seen = set()
    for action in sorted({r["action"] for r in rows}):
        count = 0
        for row in rows:
            if row["action"] == action and row["sku"] not in seen:
                seen.add(row["sku"]); chosen.append(row["sku"]); count += 1
                if count >= 10:
                    break
    for row in rows:
        if len(chosen) >= 100:
            break
        if row["sku"] not in seen:
            seen.add(row["sku"]); chosen.append(row["sku"])
    pilot_skus = set(chosen[:100])
    groups["pilot_100"] = [r for r in rows if r["sku"] in pilot_skus]
    stats = {}
    for name, items in groups.items():
        with (output / (name + ".jsonl")).open("w", encoding="utf-8") as handle:
            for item in items:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        stats[name] = {"fields": len(items), "skus": len({r["sku"] for r in items})}
    return stats


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                   default=str, separators=(",", ":")).encode()).hexdigest()


def ro(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def database_fingerprint(db):
    """Logical hashes include every row, including tables not known to this tool."""
    result = {}
    for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        quoted = '"' + table.replace('"', '""') + '"'
        rows = sorted(digest(list(row)) for row in db.execute(f"SELECT * FROM {quoted}"))
        result[table] = {"rows": len(rows), "hash": digest(rows)}
    return result


def verified_backup(database, destination):
    """SQLite backup API captures committed WAL state; restore is tested separately."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError("BACKUP_ALREADY_EXISTS")
    with ro(database) as source, sqlite3.connect(destination) as target:
        before = database_fingerprint(source)
        source.backup(target)
    restore = destination.with_suffix(".restore-check.sqlite3")
    if restore.exists():
        raise ValueError("RESTORE_CHECK_ALREADY_EXISTS")
    shutil.copy2(destination, restore)
    with ro(restore) as restored:
        integrity = restored.execute("PRAGMA integrity_check").fetchone()[0]
        fk = [list(row) for row in restored.execute("PRAGMA foreign_key_check")]
        equal = database_fingerprint(restored) == before
    if integrity != "ok" or fk or not equal:
        raise ValueError("BACKUP_RESTORE_VALIDATION_FAILED")
    return {"backup": str(destination), "restore": str(restore), "integrity": integrity,
            "foreign_key_violations": fk, "logical_parity": equal,
            "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(), "tables": before}


def choose_evidence(candidates):
    """No guessing between conflicting values or mixing versions implicitly.

    Rank only selects the evidence class. Unresolved differences within that
    class remain explicit conflicts; a date does not prove semantic equivalence.
    """
    if not candidates:
        return {"status": "SOURCE_UNAVAILABLE", "selected": None, "versions": []}
    valid = [item for item in candidates if not re.search(r"[\u3400-\u9fff\ufffd]", item["text"])]
    if not valid:
        return {"status": "SOURCE_LANGUAGE_REVIEW_REQUIRED", "selected": None, "versions": candidates}
    rank = min(item["rank"] for item in valid)
    best = [item for item in valid if item["rank"] == rank]
    values = {item["text"] for item in best}
    if len(values) != 1:
        return {"status": "SOURCE_VERSION_CONFLICT", "selected": None, "versions": candidates}
    selected = sorted(best, key=lambda item: (item["observed_at"], item["reference"]))[-1]
    return {"status": "EVIDENCE_AVAILABLE" if selected["text"] else "EXPLICIT_EMPTY_EVIDENCE",
            "selected": selected, "versions": candidates}


def add_record(index, record, *, skus, rank, reference, observed_at="", file_hash=""):
    sku = str(record.get("sku") or record.get("official_sku") or "").strip()
    if sku not in skus:
        return
    for field in FIELDS:
        source_key = CANONICAL_TO_SOURCE[field]
        if source_key not in record:
            continue
        value = record[source_key]
        text = "" if value is None else str(value)
        index[(sku, field)].append({"rank": rank, "text": text, "reference": reference,
            "observed_at": observed_at, "file_hash": file_hash,
            "field_hash": localization_field_source_hash({source_key: text}, field)})


def build_audit(database, master, snapshots, output, history_sources=None, verified_archive=None):
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    records = {str(r["sku"]): r for r in ProductionRepository(Path(database)).load_current_export_records(include_non_current=True)
               if r["status"] != "CURRENT"}
    skus = set(records); index = defaultdict(list); source_errors = []
    archived_zh = defaultdict(list)
    with ro(database) as db:
        before = database_fingerprint(db)
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        fk = [list(row) for row in db.execute("PRAGMA foreign_key_check")]
        if integrity != "ok" or fk:
            raise ValueError("PRIMARY_INTEGRITY_FAILED")
        es_skus = {str(row[0]) for row in db.execute("SELECT official_sku FROM product_localizations WHERE language='es'")}
        zh_skus = {str(row[0]) for row in db.execute("SELECT official_sku FROM product_localizations WHERE language='zh'")}
        for row in db.execute("SELECT * FROM product_fact_versions ORDER BY created_at"):
            if str(row["official_sku"]) not in skus:
                continue
            try:
                record = json.loads(row["normalized_fact_json"] or "{}")
                record["sku"] = str(row["official_sku"])
                add_record(index, record, skus=skus, rank=1,
                           reference="PRIMARY:product_fact_versions:" + str(row["fact_id"]),
                           observed_at=str(row["created_at"]), file_hash=str(row["normalized_fact_hash"] or ""))
            except (ValueError, TypeError) as exc:
                source_errors.append({"reference": str(row["fact_id"]), "error": str(exc)})
    for path in sorted(Path(snapshots).rglob("products_normalized.csv")):
        qa_path = path.parent / "qa_report.json"
        try:
            qa = json.loads(qa_path.read_text(encoding="utf-8-sig")) if qa_path.exists() else {}
            if qa.get("state") not in {"PASS", "PASS_PRESENCE_ONLY"}:
                source_errors.append({"reference": str(path), "error": "SNAPSHOT_QA_NOT_VERIFIED"})
                continue
            file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            with path.open(encoding="utf-8-sig", newline="") as handle:
                for record in csv.DictReader(handle):
                    add_record(index, record, skus=skus, rank=1, reference=str(path),
                               observed_at=path.parent.as_posix(), file_hash=file_hash)
        except (ValueError, OSError, UnicodeError, csv.Error) as exc:
            source_errors.append({"reference": str(path), "error": str(exc)})
    if history_sources:
        import yaml
        from ..exporting.history import _read_source
        config = yaml.safe_load(Path(history_sources).read_text(encoding="utf-8")) or {}
        for item in config.get("sources") or []:
            path = Path(item["path"])
            try:
                if item.get("evidence_level") != "A":
                    raise ValueError("HISTORICAL_EVIDENCE_LEVEL_NOT_VERIFIED")
                file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
                for record in _read_source(path, item):
                    # Blank workbook cells do not prove an official empty field.
                    fields = {key: value for key, value in record["fields"].items()
                              if value not in (None, "")}
                    add_record(index, {"sku": record["sku"], **fields}, skus=skus,
                               rank=2, reference=str(path) + "::" + str(item.get("sheet")),
                               observed_at=str(item.get("date") or ""), file_hash=file_hash)
            except (ValueError, OSError) as exc:
                source_errors.append({"reference": str(path), "error": str(exc)})
    from ..excel.reader import load_long_term_official
    master_records = load_long_term_official(Path(master))
    master_hash = hashlib.sha256(Path(master).read_bytes()).hexdigest()
    for sku, row in master_records.items():
        if sku not in skus:
            continue
        # Master has no description/details; absent columns are never empty facts.
        record = {"sku": sku}
        for field in ("name", "cat1", "spec"):
            key = CANONICAL_TO_SOURCE[field]
            if row.get(key) not in (None, ""):
                record[key] = row[key]
                if row.get(CANONICAL_TO_ZH[field]):
                    archived_zh[(sku, field)].append({"source": str(row[key]),
                        "target": str(row[CANONICAL_TO_ZH[field]]),
                        "reference": f"{master}::08_LONG_TERM_MASTER::SKU={sku}",
                        "file_hash": master_hash})
        add_record(index, record, skus=skus, rank=3,
                   reference=f"{master}::08_LONG_TERM_MASTER::SKU={sku}",
                   observed_at=str(row.get("last_seen") or ""), file_hash=master_hash)
    if verified_archive:
        from ..exporting.history import _read_workbook
        archive_hash = hashlib.sha256(Path(verified_archive).read_bytes()).hexdigest()
        column_map = {"name": "西班牙语品名", "cat1": "一级类目（西语）",
                      "cat2": "二级类目（西语）", "spec": "规格（西语）",
                      "description": "描述（西语）", "details": "产品详情（西语）"}
        for row in _read_workbook(Path(verified_archive), "商品全量", "编号"):
            sku = str(row.get("编号") or "").strip()
            if sku not in skus:
                continue
            if row.get("核验结论") not in {"历史官方资料已确认", "当前官网已确认"}:
                continue
            if not verified_archive_identity(row, skus):
                source_errors.append({"reference": str(verified_archive) + "::" + sku,
                                      "error": "ARCHIVE_SKU_URL_NOT_VERIFIED"})
                continue
            record = {"sku": sku}
            for field, column in column_map.items():
                if row.get(column) not in (None, ""):
                    record[CANONICAL_TO_SOURCE[field]] = row[column]
                    zh_column = {"name": "中文品名", "description": "中文产品描述",
                                 "details": "中文产品详情"}.get(field)
                    if zh_column and row.get(zh_column):
                        archived_zh[(sku, field)].append({"source": str(row[column]),
                            "target": str(row[zh_column]), "reference": str(verified_archive) + "::" + sku,
                            "file_hash": archive_hash})
            # Verification date is not the original collection date. Preserve
            # unknown collection time rather than inventing a historical date.
            add_record(index, record, skus=skus, rank=2,
                       reference=str(verified_archive) + "::" + sku, file_hash=archive_hash)
    rows = []; counts = Counter(); affected = defaultdict(set)
    for sku, record in sorted(records.items()):
        for field in FIELDS:
            source_key = CANONICAL_TO_SOURCE[field]; zh_key = CANONICAL_TO_ZH[field]
            evidence = choose_evidence(index[(sku, field)])
            target = str(record.get(zh_key) or "")
            # Existing PRIMARY text is preserved as an independent observation,
            # not silently selected as a reconstructed official version.
            source = evidence["selected"]["text"] if evidence["selected"] else ""
            retained_candidates = [item for item in archived_zh[(sku, field)] if item["source"] == source]
            candidate = retained_candidates[0]["target"] if retained_candidates else ""
            audit_target = target or candidate
            findings = []
            if evidence["status"] == "SOURCE_UNAVAILABLE":
                action = "RESTORE_SOURCE"
            elif evidence["status"] == "SOURCE_VERSION_CONFLICT":
                action = "REVIEW_SOURCE_VERSION"
            elif evidence["status"] == "SOURCE_LANGUAGE_REVIEW_REQUIRED":
                action = "REVIEW_SOURCE_LANGUAGE"
            elif not source:
                action = "REVIEW_EMPTY_SOURCE" if target else "EMPTY_EVIDENCE_REVIEW"
            elif not audit_target:
                action = "TRANSLATE_MISSING"
            else:
                qa_source = dict(record); qa_source[source_key] = source
                qa = guard_translation(SourceFacts.from_record(qa_source), {field: audit_target}, (field,))
                findings = qa["findings"]
                action = "QA_REVIEW_REQUIRED" if findings else ("RECOVERED_ZH_REVIEW_REQUIRED" if not target else "SEMANTIC_REVIEW_REQUIRED")
            row = {"sku": sku, "field": field, "status": record["status"],
                   "primary_source": record.get(source_key), "source": source,
                   "chinese": target, "evidence": evidence, "action": action,
                   "archived_chinese": archived_zh[(sku, field)], "recoverable_chinese": candidate,
                   "qa_findings": findings, "semantic_review": "NOT_RUN",
                   "freshness": record.get("zh_freshness_status") or "NO_LOCALIZATION",
                   "has_es_row": sku in es_skus, "has_zh_row": sku in zh_skus}
            provenance = (record.get("zh_field_provenance") or {}).get(field) or {}
            current_hash = localization_field_source_hash(record, field)
            row["binding"] = {"current_primary_field_hash": current_hash,
                              "bound_hash": provenance.get("source_hash"),
                              "hash_match": current_hash == provenance.get("source_hash") if provenance.get("source_hash") else None,
                              "field_freshness": provenance.get("freshness_status"),
                              "review_status": provenance.get("review_status"),
                              "approved_by": provenance.get("approved_by"),
                              "approved_at": provenance.get("approved_at")}
            rows.append(row); counts[action] += 1; affected[action].add(sku)
    with (output / "field_audit.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    with (output / "field_audit.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        keys = ("sku", "field", "status", "source", "chinese", "action", "freshness", "semantic_review")
        writer = csv.DictWriter(handle, fieldnames=keys, extrasaction="ignore"); writer.writeheader(); writer.writerows(rows)
    with ro(database) as db:
        unchanged = before == database_fingerprint(db)
    detail_stats = write_detail_reports(rows, output)
    report = {"historical_skus": len(records), "fields": len(rows),
              "statuses": dict(Counter(r["status"] for r in records.values())),
              "missing_es_rows": len(skus - es_skus), "missing_zh_rows": len(skus - zh_skus),
              "freshness": dict(Counter(r.get("zh_freshness_status") or "NO_LOCALIZATION" for r in records.values())),
              "actions": {key: {"fields": value, "skus": len(affected[key])} for key, value in counts.items()},
              "source_errors": source_errors, "primary_unchanged": unchanged,
              "integrity": integrity, "foreign_key_violations": fk,
              "semantic_review_complete": False, "applied_fields": 0,
              "master_sha256": master_hash, "tables_before": before}
    report["detail_reports"] = detail_stats
    (output / "baseline.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--master", type=Path, required=True)
    parser.add_argument("--snapshots", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--history-sources", type=Path)
    parser.add_argument("--verified-archive", type=Path)
    args = parser.parse_args()
    if args.backup:
        result = verified_backup(args.database, args.backup)
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "backup_validation.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    result = build_audit(args.database, args.master, args.snapshots, args.output,
                         args.history_sources, args.verified_archive)
    print(json.dumps({key: value for key, value in result.items() if key not in {"tables_before", "source_errors"}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
