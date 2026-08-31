from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..database.connection import connect
from ..database.schema import migrate_v2
from .contracts import ExtractionQuery, ExtractionResult


class ExtractionError(ValueError):
    pass


class ExtractionService:
    """Single read path for all product business extraction.

    This class only builds read queries; it never changes product facts or
    lifecycle decisions.  Selection/Saved View and delivery layers consume
    this contract instead of issuing their own SQL.
    """
    SORTS = {"sku": "p.official_sku", "name": "COALESCE(zh.name,es.name,p.name_es)",
             "current_price": "p.current_price", "first_seen": "p.first_seen_at",
             "last_seen": "p.last_seen_at", "recent_change": "recent_change"}
    STATUS_MAP = {"CURRENT": "CURRENT", "MISSING": "MISSING", "OFFLINE": "OFFLINE"}

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise ExtractionError("DB_MISSING")
        migrate_v2(self.db_path, role="PRIMARY")

    def execute(self, query: ExtractionQuery | dict[str, Any] | None = None) -> ExtractionResult:
        q = query if isinstance(query, ExtractionQuery) else ExtractionQuery.from_dict(query or {})
        if q.limit < 1 or q.limit > 10000 or q.offset < 0:
            raise ExtractionError("PAGINATION_INVALID")
        if q.sort not in self.SORTS:
            raise ExtractionError(f"SORT_UNSUPPORTED: {q.sort}")
        clauses: list[str] = []
        args: list[Any] = []
        joins = ["LEFT JOIN product_localizations es ON es.official_sku=p.official_sku AND es.language='es'",
                 "LEFT JOIN product_localizations zh ON zh.official_sku=p.official_sku AND zh.language='zh'",
                 "LEFT JOIN image_assets ia ON ia.official_sku=p.official_sku",
                 "LEFT JOIN (SELECT h.official_sku,h.observed_at AS latest_at,h.new_price-h.old_price AS change_amount, CASE WHEN h.new_price>h.old_price THEN 'UP' WHEN h.new_price<h.old_price THEN 'DOWN' ELSE 'FLAT' END AS change_direction, CASE WHEN h.old_price IS NULL OR h.old_price=0 THEN NULL ELSE (h.new_price-h.old_price)*100.0/h.old_price END AS change_percent FROM price_history h WHERE h.observed_at=(SELECT MAX(h2.observed_at) FROM price_history h2 WHERE h2.official_sku=h.official_sku)) ph ON ph.official_sku=p.official_sku"]
        if q.keyword:
            term = f"%{' '.join(q.keyword.lower().split())}%"
            clauses.append("(lower(p.official_sku) LIKE ? OR lower(COALESCE(p.name_es,'')) LIKE ? OR lower(COALESCE(p.name_zh,'')) LIKE ? OR lower(COALESCE(es.name,'')) LIKE ? OR lower(COALESCE(zh.name,'')) LIKE ?)")
            args.extend([term] * 5)
        self._in_clause(clauses, args, "p.official_sku", q.skus)
        statuses = tuple(self.STATUS_MAP.get(str(s).upper(), str(s).upper()) for s in q.statuses)
        self._in_clause(clauses, args, "p.status", statuses)
        self._in_clause(clauses, args, "COALESCE(zh.cat1,es.cat1,'')", q.cat1)
        self._in_clause(clauses, args, "COALESCE(zh.cat2,es.cat2,'')", q.cat2)
        if q.min_price is not None: clauses.append("p.current_price >= ?"); args.append(q.min_price)
        if q.max_price is not None: clauses.append("p.current_price <= ?"); args.append(q.max_price)
        if q.has_original_price is True: clauses.append("p.original_price IS NOT NULL AND p.original_price > p.current_price")
        elif q.has_original_price is False: clauses.append("(p.original_price IS NULL OR p.original_price <= p.current_price)")
        for field, value in (("p.promotion_active", q.promotion), ("p.action_new_badge", q.new_badge), ("p.sustainable_badge", q.sustainable)):
            if value is not None: clauses.append(f"{field}=?"); args.append(int(value))
        if q.image_statuses: self._in_clause(clauses, args, "COALESCE(ia.status,'NO_SOURCE_URL')", q.image_statuses)
        if q.has_image is True: clauses.append("ia.status='AVAILABLE'")
        elif q.has_image is False: clauses.append("(ia.status IS NULL OR ia.status<>'AVAILABLE')")
        if q.localization_status:
            clauses.append("CASE WHEN COALESCE(zh.name,'')<>'' AND COALESCE(zh.cat1,'')<>'' AND COALESCE(zh.spec,'')<>'' THEN 'COMPLETE' ELSE 'INCOMPLETE' END=?"); args.append(q.localization_status.upper())
        for field in q.missing_fields:
            column = {"category": "COALESCE(zh.cat1,es.cat1)", "spec": "COALESCE(zh.spec,es.spec)", "description": "COALESCE(zh.description,es.description)", "image": "ia.status", "localization": "zh.name"}.get(field)
            if not column: raise ExtractionError(f"MISSING_FIELD_UNSUPPORTED: {field}")
            clauses.append(f"({column} IS NULL OR trim({column})='')")
        if q.price_change: clauses.append("ph.change_direction=?"); args.append(q.price_change.upper())
        if q.min_change_amount is not None: clauses.append("abs(COALESCE(ph.change_amount,0)) >= ?"); args.append(q.min_change_amount)
        if q.min_change_percent is not None: clauses.append("abs(COALESCE(ph.change_percent,0)) >= ?"); args.append(q.min_change_percent)
        if q.event_types:
            placeholders = ",".join("?" for _ in q.event_types); clauses.append(f"EXISTS (SELECT 1 FROM event_history ev WHERE ev.official_sku=p.official_sku AND ev.event_type IN ({placeholders}))"); args.extend(q.event_types)
        if q.date_from: clauses.append("COALESCE(p.last_seen_at,p.last_checked_at) >= ?"); args.append(q.date_from)
        if q.date_to: clauses.append("COALESCE(p.last_seen_at,p.last_checked_at) <= ?"); args.append(q.date_to)
        if q.last_n_days is not None:
            since = (datetime.now(timezone.utc) - timedelta(days=int(q.last_n_days))).date().isoformat(); clauses.append("COALESCE(p.last_seen_at,p.last_checked_at) >= ?"); args.append(since)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        sort_expr = self.SORTS[q.sort]
        direction = "DESC" if q.descending else "ASC"
        select = """SELECT p.canonical_id,p.official_sku,p.name_es,p.current_price,p.original_price,p.status,p.product_url,p.first_seen_at,p.last_seen_at,p.action_new_badge,p.promotion_active,p.sustainable_badge,
                   es.name AS es_name,es.cat1 AS es_cat1,es.cat2 AS es_cat2,es.spec AS es_spec,es.description AS es_description,es.details AS es_details,
                   zh.name AS zh_name,zh.cat1 AS zh_cat1,zh.cat2 AS zh_cat2,zh.spec AS zh_spec,zh.description AS zh_description,zh.details AS zh_details,
                   ia.status AS image_status, ph.change_direction,ph.change_amount,ph.change_percent"""
        base = f" FROM products p {' '.join(joins)}{where}"
        with connect(self.db_path) as db:
            total = int(db.execute(f"SELECT COUNT(DISTINCT p.official_sku){base}", args).fetchone()[0])
            rows = db.execute(f"{select}{base} ORDER BY {sort_expr} {direction},p.official_sku ASC LIMIT ? OFFSET ?", [*args, q.limit, q.offset]).fetchall()
            source = db.execute("SELECT commit_id FROM commit_batches WHERE status='COMMITTED' ORDER BY committed_at DESC LIMIT 1").fetchone()
        items = tuple(self._row(row) for row in rows)
        return ExtractionResult(q.normalized(), q.query_hash(), total, items,
            {"field": q.sort, "descending": q.descending, "secondary": "sku"},
            {"limit": q.limit, "offset": q.offset, "returned": len(items), "has_more": q.offset + len(items) < total},
            str(source[0]) if source else None, datetime.now(timezone.utc).isoformat())

    @staticmethod
    def _in_clause(clauses: list[str], args: list[Any], expression: str, values: tuple[str, ...]) -> None:
        if values:
            placeholders = ",".join("?" for _ in values); clauses.append(f"{expression} IN ({placeholders})"); args.extend(values)

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        keys = list(row.keys()); data = {key: row[key] for key in keys}
        for key in ("action_new_badge", "promotion_active", "sustainable_badge"):
            data[key] = bool(data[key])
        data["localization_status"] = "COMPLETE" if all(str(data.get(k) or "").strip() for k in ("zh_name", "zh_cat1", "zh_spec")) else "INCOMPLETE"
        data["has_image"] = data.get("image_status") == "AVAILABLE"
        return data
