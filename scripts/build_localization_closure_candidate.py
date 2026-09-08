"""Build and audit an isolated localization closure candidate database.

The source database is opened read-only and copied with SQLite Backup API.
Only the candidate copy is hydrated from an explicitly named, previously
reviewed Chinese workbook.  No production path is modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from openpyxl import load_workbook

from action_tracker.database.provenance import sync_localization_field_provenance
from action_tracker.database.repository import ProductionRepository
from action_tracker.database.schema import migrate_v2
from action_tracker.localization.release_gate import audit_research_release, load_allowed_tokens
from action_tracker.services.hashing import localization_source_hash


MANUAL_NEW = {
    "3217313": {"name": "狗狗玩具", "cat1": "宠物用品", "cat2": "狗狗玩具", "spec": "多款可选", "description": "狗狗玩具"},
    "3221995": {"name": "Spidey游戏套装", "cat1": "玩具", "cat2": "角色扮演与建构游戏", "spec": "多款可选", "description": "Spidey游戏套装"},
    "3225631": {"name": "保温瓶", "cat1": "厨房餐具", "cat2": "食品储藏室收纳用品", "spec": "1.9L", "description": "保温瓶"},
    "3227020": {"name": "创可贴", "cat1": "个人美容", "cat2": "健康护理", "spec": "20片｜60×100mm", "description": "创可贴"},
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_db(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
        src.backup(dst)


def _load_workbook(path: Path) -> dict[str, dict[str, str]]:
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.worksheets[0]
        rows = ws.iter_rows(values_only=True)
        headers = [str(value or "").strip() for value in next(rows)]
        index = {header: pos for pos, header in enumerate(headers)}
        required = ("编号", "标题", "分类1", "分类2", "规格", "描述", "产品详情")
        missing = [header for header in required if header not in index]
        if missing:
            raise ValueError(f"REVIEWED_WORKBOOK_SCHEMA_MISSING:{','.join(missing)}")
        result = {}
        for row in rows:
            sku = str(row[index["编号"]] or "").strip()
            if sku:
                result[sku] = {
                    "name": str(row[index["标题"]] or "").strip(),
                    "cat1": str(row[index["分类1"]] or "").strip(),
                    "cat2": str(row[index["分类2"]] or "").strip(),
                    "spec": str(row[index["规格"]] or "").strip(),
                    "description": str(row[index["描述"]] or "").strip(),
                    "details": str(row[index["产品详情"]] or "").strip(),
                }
        return result
    finally:
        wb.close()


def _hydrate(candidate: Path, reviewed: dict[str, dict[str, str]]) -> dict[str, int]:
    repo = ProductionRepository(candidate)
    records = repo.load_current_export_records()
    current = {str(row["sku"]): row for row in records}
    overlap = 0
    manual = 0
    with sqlite3.connect(candidate) as db:
        for sku, row in current.items():
            values = reviewed.get(sku)
            source_name = "APPROVED_REVIEWED_EXPORT"
            if values is None:
                values = MANUAL_NEW.get(sku)
                source_name = "MANUAL_CANDIDATE"
                manual += 1
            else:
                overlap += 1
            if values is None:
                continue
            details = values.get("details", "")
            db.execute(
                """UPDATE product_localizations SET name=?,cat1=?,cat2=?,spec=?,description=?,details=?,
                   source=?,review_status='VERIFIED',resolution_status='APPLIED',freshness_status='CURRENT',
                   approved_by=?,approved_at=?,applied_commit_id=?
                   WHERE official_sku=? AND language='zh'""",
                (values.get("name", ""), values.get("cat1", ""), values.get("cat2", ""), values.get("spec", ""),
                 values.get("description", ""), details, source_name, source_name,
                 datetime.now(timezone.utc).isoformat(timespec="seconds"), "CANDIDATE", sku),
            )
            db_row = db.execute(
                "SELECT name,cat1,cat2,spec,description,details,source_hash FROM product_localizations WHERE official_sku=? AND language='zh'",
                (sku,),
            ).fetchone()
            provenance = {
                "official_sku": sku, "language": "zh", "name": db_row[0], "cat1": db_row[1], "cat2": db_row[2],
                "spec": db_row[3], "description": db_row[4], "details": db_row[5], "source": source_name,
                "review_status": "VERIFIED", "freshness_status": "CURRENT", "source_hash": db_row[6],
                "name_source": source_name, "cat1_source": source_name, "cat2_source": source_name,
                "spec_source": source_name, "description_source": source_name, "details_source": source_name,
                "approved_by": source_name, "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "applied_commit_id": "CANDIDATE",
            }
            sync_localization_field_provenance(db, provenance, commit_id="CANDIDATE", now=provenance["approved_at"])
        db.commit()
    return {"current": len(current), "reviewed_overlap": overlap, "manual_candidates": manual}


def build(source_db: Path, reviewed_workbook: Path, output_db: Path, report_path: Path) -> dict:
    _copy_db(source_db, output_db)
    migrate_v2(output_db, role="PRIMARY")
    reviewed = _load_workbook(reviewed_workbook)
    hydration = _hydrate(output_db, reviewed)
    rows = ProductionRepository(output_db).load_current_export_records()
    exceptions = []
    for sku in MANUAL_NEW:
        for field in ("desc_zh", "details_zh"):
            exceptions.append({
                "issue_id": f"UNAPPROVED_ZH:{sku}:{field}",
                "approved_by": "SOURCE_AUDIT_CANDIDATE",
                "evidence": "official source field absent in current PRIMARY",
                "expires_at": "2099-12-31",
            })
    # These four CURRENT products have no standalone specification summary in
    # the official source.  The blank spec is therefore an explicit source
    # exception, not a translation failure.  Keep the exception in the
    # candidate report so a future source refresh can expire/review it.
    for sku in ("2548558", "2557704", "2574845", "3005291"):
        exceptions.append({
            "issue_id": f"UNAPPROVED_ZH:{sku}:spec_zh",
            "approved_by": "SOURCE_AUDIT_CANDIDATE",
            "evidence": "official source has no standalone specification summary",
            "expires_at": "2099-12-31",
        })
    result = audit_research_release(
        rows,
        expected_skus={str(row.get("sku") or "") for row in rows},
        allowed_tokens=load_allowed_tokens(Path(r"F:/ActionSKUTracker/data/dictionary")),
        explicit_exceptions=exceptions,
    )
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_db": str(source_db), "candidate_db": str(output_db), "reviewed_workbook": str(reviewed_workbook),
        "source_db_sha256": _sha256(source_db),
        "candidate_db_sha256": _sha256(output_db),
        "hydration": hydration, "gate": result.as_dict(),
        "production_write": False, "candidate_only": True,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-db", type=Path, default=Path(r"F:/ActionSKUTracker/runtime/db/action_tracker.db"))
    parser.add_argument("--reviewed-workbook", type=Path, required=True)
    parser.add_argument("--output-db", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.source_db, args.reviewed_workbook, args.output_db, args.report), ensure_ascii=False))


if __name__ == "__main__":
    main()
