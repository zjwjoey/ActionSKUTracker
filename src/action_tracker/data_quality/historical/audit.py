from __future__ import annotations

"""Read-only historical fact audit.

The audit emits issues; it never repairs a product, localization, price or
event row.  This module intentionally uses table introspection so a SQLite
Backup from an older closure schema can still be inspected safely.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable

from ...database.connection import connect
from ..contracts import DataQualityIssue, IssueScope
from ..repository import DataQualityRepository

_HTML_RE = re.compile(r"<\/?[A-Za-z][^>]*>", re.I)
_UI_TEXT = {"añadir a tus favoritos", "leer más", "descripción"}
_UNIT_PRICE_RE = re.compile(r"(?:€/|€\s*/|/\s*(?:kg|l|ud\.?|unidad))", re.I)


@dataclass(frozen=True)
class AuditResult:
    scope: str
    issues: tuple[DataQualityIssue, ...]
    database: str
    persisted: bool = False

    @property
    def counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for issue in self.issues:
            result[issue.issue_type] = result.get(issue.issue_type, 0) + 1
        return dict(sorted(result.items()))

    @property
    def ok(self) -> bool:
        return not any(issue.severity in {"BLOCKER", "HIGH"} for issue in self.issues)

    def as_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "database": self.database,
            "issue_count": len(self.issues),
            "counts": self.counts,
            "issues": [issue.as_dict() for issue in self.issues],
            "persisted": self.persisted,
            "status": "PASS" if self.ok else "FAIL",
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _tables(db: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})")}


def _issue(*, issue_type: str, severity: str, sku: str | None = None,
           canonical_id: str | None = None, field: str | None = None,
           current: Any = None, rule: str, evidence: dict[str, Any],
           source_hash: str | None = None, run_id: str | None = None) -> DataQualityIssue:
    return DataQualityIssue(
        issue_type=issue_type, severity=severity, scope=IssueScope.HISTORICAL.value,
        official_sku=sku or None, canonical_id=canonical_id or None, run_id=run_id or None,
        field_name=field or None, current_value=current, expected_rule=rule,
        evidence={**evidence, "detected_at": _now()}, source_hash=source_hash, detected_at=_now(),
    )


def audit_history(db_path: Path, *, persist: bool = False, issue_types: Iterable[str] | None = None) -> AuditResult:
    """Audit a SQLite database or read-only backup and return stable issues."""
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(path)
    wanted = {str(item) for item in issue_types} if issue_types else None
    found: list[DataQualityIssue] = []

    def add(**kwargs: Any) -> None:
        if wanted is None or kwargs["issue_type"] in wanted:
            found.append(_issue(**kwargs))

    # An audit must not even acquire a write-capable SQLite connection.  WAL
    # setup in the normal connection helper is a write, so use SQLite's URI
    # read-only mode for the inspection itself.  Persistence is a separate,
    # explicit repository operation below.
    ro = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    try:
        db = ro
        tables = _tables(db)
        product_columns = _columns(db, "products") if "products" in tables else set()

        # H01: invalid original price. Missing official original price is not
        # an issue; only an observed non-null value that is not strictly above
        # current price is flagged.
        if {"current_price", "original_price", "official_sku"}.issubset(product_columns):
            for row in db.execute("SELECT official_sku,canonical_id,current_price,original_price FROM products WHERE current_price IS NOT NULL AND original_price IS NOT NULL AND original_price <= current_price"):
                add(issue_type="INVALID_ORIGINAL_PRICE", severity="HIGH", sku=str(row[0]), canonical_id=row[1],
                    field="original_price", current=row[3], rule="original_price > current_price",
                    evidence={"current_price": row[2], "original_price": row[3]})

        # H02: unit price or workflow text leaking into the badge/promotion
        # channel.  Official raw badge strings are evidence, but not promo
        # semantics.
        if "raw_badges" in product_columns:
            for row in db.execute("SELECT official_sku,canonical_id,raw_badges FROM products WHERE raw_badges IS NOT NULL AND TRIM(raw_badges)<>''"):
                value = str(row[2])
                if _UNIT_PRICE_RE.search(value) or any(token in value.casefold() for token in ("workflow", "本期详情", "新商品", "sustainability")):
                    add(issue_type="PROMOTION_FIELD_CONTAMINATION", severity="HIGH", sku=str(row[0]), canonical_id=row[1],
                        field="raw_badges", current=value, rule="promotion/badges contain only official badge facts",
                        evidence={"value": value})

        # H03/H04 operate on normalized official localization fields. Raw
        # source_fact evidence is explicitly allowed to contain HTML.
        localization_tables: list[tuple[str, str, str]] = []
        if "product_localizations" in tables:
            localization_tables.append(("product_localizations", "official_sku", "language"))
        if "localization_fields" in tables:
            localization_tables.append(("localization_fields", "official_sku", "language"))
        for table, sku_col, lang_col in localization_tables:
            cols = _columns(db, table)
            if "value" in cols:
                source_select = ",source_hash" if "source_hash" in cols else ""
                rows = db.execute(f"SELECT {sku_col},{lang_col},field_name,value{source_select} FROM {table} WHERE value IS NOT NULL AND TRIM(value)<>''")
                for row in rows:
                    sku, lang, field, value = str(row[0]), str(row[1]), str(row[2]), str(row[3])
                    source_hash = None
                    if "source_hash" in cols:
                        source_hash = str(row[4] or "") or None
                    if lang == "es" and (_HTML_RE.search(value) or "</" in value or "<" in value):
                        add(issue_type="HTML_CONTAMINATION", severity="HIGH", sku=sku, field=field, current=value,
                            rule="normalized official facts must not retain HTML", evidence={"table": table, "language": lang}, source_hash=source_hash)
                    if value.strip().casefold() in _UI_TEXT:
                        add(issue_type="UI_TEXT_CONTAMINATION", severity="HIGH", sku=sku, field=field, current=value,
                            rule="transport/UI labels cannot be normalized product facts", evidence={"table": table, "language": lang}, source_hash=source_hash)
            else:
                fields = [name for name in ("name", "cat1", "cat2", "spec", "description", "details") if name in cols]
                if not fields:
                    continue
                source_select = ",source_hash" if "source_hash" in cols else ""
                select = ",".join(fields) + source_select
                for row in db.execute(f"SELECT official_sku,language,{select} FROM {table}"):
                    sku, lang = str(row[0]), str(row[1])
                    for index, field in enumerate(fields, 2):
                        value = row[index]
                        if value is None:
                            continue
                        text = str(value)
                        if lang == "es" and (_HTML_RE.search(text) or "</" in text or "<" in text):
                            add(issue_type="HTML_CONTAMINATION", severity="HIGH", sku=sku, field=field, current=text,
                                rule="normalized official facts must not retain HTML", evidence={"table": table, "language": lang}, source_hash=source_hash)
                        source_hash = None
                        if "source_hash" in cols:
                            source_hash = str(row[2 + len(fields)] or "") or None
                        if text.strip().casefold() in _UI_TEXT:
                            add(issue_type="UI_TEXT_CONTAMINATION", severity="HIGH", sku=sku, field=field, current=text,
                                rule="transport/UI labels cannot be normalized product facts", evidence={"table": table, "language": lang}, source_hash=source_hash)

        # H08 category backlog.  This is a warning: missing source evidence is
        # explicit uncertainty, not a guessed category.
        if "product_localizations" in tables:
            cols = _columns(db, "product_localizations")
            if {"cat1", "cat2"}.issubset(cols):
                for row in db.execute("SELECT official_sku,cat1,cat2 FROM product_localizations WHERE language='es' AND TRIM(COALESCE(cat1,''))<>'' AND TRIM(COALESCE(cat2,''))=''" ):
                    add(issue_type="CATEGORY_MISSING", severity="MEDIUM", sku=str(row[0]), field="cat2", current=row[2],
                        rule="cat2 may be absent only with explicit backlog evidence", evidence={"cat1": row[1]})

        # H06/H07: history is formal only when it has an existing product.
        for table, issue_type in (("price_history", "ORPHAN_PRICE_HISTORY"), ("event_history", "ORPHAN_EVENT_HISTORY")):
            if table not in tables or "products" not in tables:
                continue
            cols = _columns(db, table)
            if "official_sku" not in cols:
                continue
            for row in db.execute(f"SELECT h.official_sku FROM {table} h LEFT JOIN products p ON p.official_sku=h.official_sku WHERE h.official_sku IS NULL OR p.official_sku IS NULL"):
                add(issue_type=issue_type, severity="HIGH", sku=str(row[0] or ""),
                    rule="formal history must reference an authoritative product", evidence={"table": table})

        # H05: source records can preserve an unresolved synthetic archive row;
        # it must remain isolated rather than receiving a guessed SKU.
        if "source_records" in tables:
            cols = _columns(db, "source_records")
            if {"official_sku", "payload_json"}.issubset(cols):
                for row in db.execute("SELECT source_record_id,payload_json FROM source_records WHERE official_sku IS NULL OR TRIM(official_sku)=''" ):
                    try:
                        payload = json.loads(row[1] or "{}")
                    except (TypeError, ValueError):
                        payload = {}
                    if payload.get("canonical_id") or payload.get("synthetic_id"):
                        add(issue_type="UNRESOLVED_HISTORICAL_IDENTITY", severity="HIGH", field="official_sku",
                            current=None, rule="do not infer an official SKU without authoritative evidence",
                            evidence={"source_record_id": row[0], "payload_keys": sorted(payload)})

    finally:
        ro.close()

    # Stable de-duplication is important when legacy compatibility tables are
    # both present; the same issue must not be emitted twice.
    unique: dict[str, DataQualityIssue] = {item.issue_id: item for item in found}
    issues = tuple(unique[key] for key in sorted(unique))
    persisted = False
    if persist:
        DataQualityRepository(path).save_issues(issues)
        persisted = True
    return AuditResult(IssueScope.HISTORICAL.value, issues, str(path), persisted)
