from __future__ import annotations

"""Read-only quality gate for the formal SQLite dataset."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

_HTML_RE = re.compile(r"<\/?[A-Za-z][^>]*>", re.I)
_UNIT_PRICE_RE = re.compile(r"(?:€/|€\s*/|/\s*(?:kg|l|ud\.?|unidad))", re.I)
_PROMOTION_TEXT_COLUMNS = frozenset({"promotion", "promotion_status", "promotion_label", "promotion_text", "promotion_note"})
_PROMOTION_CONTAMINATION_TOKENS = (
    "workflow", "本期详情", "nuevo producto", "nuevo", "sostenible", "sostenibilidad",
    "sustainability", "descuento", "discount",
)
_UI_TEXT = ("añadir a tus favoritos", "leer más", "descripción")
_FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")


def _read_only(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{Path(path).resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _tables(db: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _cols(db: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})")}


@dataclass(frozen=True)
class MasterQualityResult:
    status: str
    release_ready: bool
    commit_id: str | None
    counts: dict[str, int]
    issues: tuple[str, ...]
    warnings: tuple[str, ...]
    records_checked: int
    generated_at: str

    @property
    def ok(self) -> bool:
        return self.release_ready

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status, "release_ready": self.release_ready, "ok": self.ok,
            "commit_id": self.commit_id, "generated_at": self.generated_at,
            "records_checked": self.records_checked, "counts": dict(self.counts),
            "issues": list(self.issues), "warnings": list(self.warnings),
        }


def audit_master_quality(db_path: Path) -> MasterQualityResult:
    """Audit current formal data without migrations or writes."""
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(path)
    issues: list[str] = []
    warnings: list[str] = []
    counts: dict[str, int] = {}

    def issue(code: str, detail: str | None = None) -> None:
        counts[code] = counts.get(code, 0) + 1
        issues.append(f"{code}:{detail}" if detail else code)

    def warn(code: str, detail: str | None = None) -> None:
        counts[code] = counts.get(code, 0) + 1
        warnings.append(f"{code}:{detail}" if detail else code)

    with _read_only(path) as db:
        tables = _tables(db)
        if "products" not in tables:
            issue("CURRENT_WITHOUT_SKU", "PRODUCT_TABLE_MISSING")
            return MasterQualityResult("FAIL", False, None, counts, tuple(issues), tuple(warnings), 0, datetime.now(timezone.utc).isoformat(timespec="seconds"))
        product_cols = _cols(db, "products")
        current_rows = db.execute("SELECT * FROM products WHERE status='CURRENT'").fetchall()
        records_checked = len(current_rows)
        sku_values = [str(row["official_sku"] or "").strip() for row in current_rows]
        seen: dict[str, int] = {}
        for sku in sku_values:
            seen[sku] = seen.get(sku, 0) + 1
        for sku, count in seen.items():
            if not sku:
                issue("CURRENT_WITHOUT_SKU")
            elif count > 1:
                issue("DUPLICATE_SKU", sku)

        if {"current_price", "original_price"}.issubset(product_cols):
            for row in current_rows:
                if row["current_price"] is not None and row["original_price"] is not None and row["original_price"] <= row["current_price"]:
                    issue("INVALID_ORIGINAL_PRICE", str(row["official_sku"]))
        if "raw_badges" in product_cols:
            for row in current_rows:
                text = str(row["raw_badges"] or "")
                if _UNIT_PRICE_RE.search(text) or any(token in text.casefold() for token in ("workflow", "本期详情")):
                    issue("PROMOTION_FIELD_CONTAMINATION", str(row["official_sku"]))
        for column in sorted(product_cols & _PROMOTION_TEXT_COLUMNS):
            for row in current_rows:
                text = str(row[column] or "")
                if _UNIT_PRICE_RE.search(text) or any(token in text.casefold() for token in _PROMOTION_CONTAMINATION_TOKENS):
                    issue("PROMOTION_FIELD_CONTAMINATION", f"{row['official_sku']}:{column}")

        # The ES/ZH SKU sets are compared only when their formal projections
        # exist.  A missing projection is itself a provenance failure below.
        es_skus: set[str] = set(); zh_skus: set[str] = set()
        if "product_localizations" in tables:
            es_skus = {str(row[0]) for row in db.execute("SELECT official_sku FROM product_localizations WHERE language='es'")}
            zh_skus = {str(row[0]) for row in db.execute("SELECT official_sku FROM product_localizations WHERE language='zh'")}
            for sku in sorted(es_skus ^ zh_skus):
                issue("ZH_ES_SKU_SET_MISMATCH", sku)
            loc_cols = _cols(db, "product_localizations")
            if {"language", "cat1", "cat2"}.issubset(loc_cols):
                for row in db.execute("SELECT official_sku,cat1,cat2 FROM product_localizations WHERE language='zh' AND TRIM(COALESCE(cat1,''))<>'' AND TRIM(COALESCE(cat2,''))=''" ):
                    warn("CATEGORY_MISSING", str(row[0]))
            if {"language", "description", "details"}.issubset(loc_cols):
                for row in db.execute("SELECT official_sku,description,details FROM product_localizations WHERE language='zh' AND (TRIM(COALESCE(description,''))='' OR TRIM(COALESCE(details,''))='')"):
                    warn("DESCRIPTION_OR_DETAILS_MISSING", str(row[0]))
            if {"language", *(_FIELDS)}.issubset(loc_cols):
                for row in db.execute("SELECT official_sku," + ",".join(_FIELDS) + " FROM product_localizations WHERE language='es'"):
                    for index, field in enumerate(_FIELDS, 1):
                        text = str(row[index] or "")
                        if _HTML_RE.search(text):
                            issue("HTML_CONTAMINATION", f"{row[0]}:{field}")
                        if text.strip().casefold() in _UI_TEXT:
                            issue("UI_TEXT_CONTAMINATION", f"{row[0]}:{field}")
        else:
            issue("ZH_ES_SKU_SET_MISMATCH", "LOCALIZATION_TABLE_MISSING")

        # Field-level provenance and source hashes are hard requirements for
        # current localized facts.  Missing tables are not silently downgraded.
        provenance_table = "localization_fields" if "localization_fields" in tables else ("localization_field_provenance" if "localization_field_provenance" in tables else None)
        if not provenance_table:
            issue("FIELD_PROVENANCE_MISSING", "TABLE_MISSING")
            issue("SOURCE_HASH_MISSING", "TABLE_MISSING")
        else:
            pcols = _cols(db, provenance_table)
            if not {"official_sku", "language", "field_name", "source_hash"}.issubset(pcols):
                issue("FIELD_PROVENANCE_MISSING", "SCHEMA")
                issue("SOURCE_HASH_MISSING", "SCHEMA")
            else:
                for sku in sorted(set(sku_values)):
                    if not sku:
                        continue
                    rows = db.execute(f"SELECT field_name,source_hash FROM {provenance_table} WHERE official_sku=? AND language='zh'", (sku,)).fetchall()
                    by_field = {str(row[0]): str(row[1] or "").strip() for row in rows}
                    for field in _FIELDS:
                        if field not in by_field:
                            issue("FIELD_PROVENANCE_MISSING", f"{sku}:{field}")
                        if not by_field.get(field):
                            issue("SOURCE_HASH_MISSING", f"{sku}:{field}")

        for table, code in (("price_history", "ORPHAN_FORMAL_PRICE_HISTORY"), ("event_history", "ORPHAN_FORMAL_EVENT_HISTORY")):
            if table not in tables:
                continue
            columns = _cols(db, table)
            if "official_sku" not in columns:
                continue
            for row in db.execute(f"SELECT h.official_sku FROM {table} h LEFT JOIN products p ON p.official_sku=h.official_sku WHERE h.official_sku IS NULL OR p.official_sku IS NULL"):
                issue(code, str(row[0] or ""))
        if "image_assets" in tables:
            for row in db.execute("SELECT official_sku FROM image_assets WHERE status NOT IN ('AVAILABLE','READY')"):
                warn("IMAGE_MISSING", str(row[0]))

        commit_id = None
        if "commit_batches" in tables:
            row = db.execute("SELECT commit_id FROM commit_batches WHERE status='COMMITTED' ORDER BY committed_at DESC,commit_id DESC LIMIT 1").fetchone()
            commit_id = str(row[0]) if row else None

    blocking = tuple(dict.fromkeys(issues))
    warnings_u = tuple(dict.fromkeys(warnings))
    return MasterQualityResult("PASS" if not blocking else "FAIL", not blocking, commit_id, counts, blocking, warnings_u, records_checked, datetime.now(timezone.utc).isoformat(timespec="seconds"))


def format_master_quality(result: MasterQualityResult) -> str:
    def count(code: str) -> int:
        return int(result.counts.get(code, 0))
    return "\n".join((
        "MASTER QUALITY REPORT", "", f"commit: {result.commit_id or '-'}", f"generated_at: {result.generated_at}", "",
        f"Integrity             {'PASS' if result.release_ready else 'FAIL'}",
        f"Price Semantics       {'PASS' if not any(k in result.counts for k in ('INVALID_ORIGINAL_PRICE','PROMOTION_FIELD_CONTAMINATION')) else 'FAIL'}",
        f"Text Cleanliness      {'PASS' if not any(k in result.counts for k in ('UI_TEXT_CONTAMINATION','HTML_CONTAMINATION')) else 'FAIL'}",
        f"History Integrity     {'PASS' if not any(k in result.counts for k in ('ORPHAN_FORMAL_PRICE_HISTORY','ORPHAN_FORMAL_EVENT_HISTORY')) else 'FAIL'}",
        f"Localization          {'PASS' if not any(k in result.counts for k in ('ZH_ES_SKU_SET_MISMATCH','FIELD_PROVENANCE_MISSING')) else 'FAIL'}",
        f"Provenance            {'PASS' if not any(k in result.counts for k in ('FIELD_PROVENANCE_MISSING','SOURCE_HASH_MISSING')) else 'FAIL'}",
        f"Freshness             {'WARN' if result.warnings else 'PASS'}", "",
        f"BLOCKER: {count('DUPLICATE_SKU') + count('CURRENT_WITHOUT_SKU')}",
        f"HIGH: {sum(count(k) for k in ('INVALID_ORIGINAL_PRICE','UI_TEXT_CONTAMINATION','HTML_CONTAMINATION','PROMOTION_FIELD_CONTAMINATION','ORPHAN_FORMAL_PRICE_HISTORY','ORPHAN_FORMAL_EVENT_HISTORY','FIELD_PROVENANCE_MISSING','SOURCE_HASH_MISSING','ZH_ES_SKU_SET_MISMATCH'))}",
        f"MEDIUM: {sum(value for key, value in result.counts.items() if key in {'CATEGORY_MISSING','DESCRIPTION_OR_DETAILS_MISSING','SOURCE_FRESHNESS','LEGACY_SOURCE_EVIDENCE_MISSING'})}",
        f"LOW: {sum(value for key, value in result.counts.items() if key not in {'DUPLICATE_SKU','CURRENT_WITHOUT_SKU','INVALID_ORIGINAL_PRICE','UI_TEXT_CONTAMINATION','HTML_CONTAMINATION','PROMOTION_FIELD_CONTAMINATION','ORPHAN_FORMAL_PRICE_HISTORY','ORPHAN_FORMAL_EVENT_HISTORY','FIELD_PROVENANCE_MISSING','SOURCE_HASH_MISSING','ZH_ES_SKU_SET_MISMATCH','CATEGORY_MISSING','DESCRIPTION_OR_DETAILS_MISSING','SOURCE_FRESHNESS','LEGACY_SOURCE_EVIDENCE_MISSING'})}",
        f"WARN: {len(result.warnings)}", f"RELEASE_READY: {'YES' if result.release_ready else 'NO'}",
    ))
