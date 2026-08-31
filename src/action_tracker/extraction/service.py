from __future__ import annotations

import sqlite3
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..database.connection import connect
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
             "last_seen": "p.last_seen_at", "recent_change": "COALESCE(ph.latest_at,'')"}
    STATUS_MAP = {"CURRENT": "CURRENT", "MISSING": "MISSING", "OFFLINE": "OFFLINE"}

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise ExtractionError("DB_MISSING")

    def execute(self, query: ExtractionQuery | dict[str, Any] | None = None, *, connection: sqlite3.Connection | None = None) -> ExtractionResult:
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
                 "LEFT JOIN (SELECT official_sku,observed_at AS latest_at,new_price-old_price AS change_amount, CASE WHEN new_price>old_price THEN 'UP' WHEN new_price<old_price THEN 'DOWN' ELSE 'FLAT' END AS change_direction, CASE WHEN old_price IS NULL OR old_price=0 THEN NULL ELSE (new_price-old_price)*100.0/old_price END AS change_percent FROM (SELECT h.*,ROW_NUMBER() OVER (PARTITION BY h.official_sku ORDER BY h.observed_at DESC,h.id DESC) AS rn FROM price_history h) ranked WHERE rn=1) ph ON ph.official_sku=p.official_sku"]
        joins.append("LEFT JOIN (SELECT official_sku,event_type AS recent_event_type,occurred_at AS recent_event_at FROM (SELECT e.official_sku,e.event_type,e.occurred_at,ROW_NUMBER() OVER (PARTITION BY e.official_sku ORDER BY e.occurred_at DESC,e.id DESC) AS rn FROM event_history e) ranked WHERE rn=1) ev_latest ON ev_latest.official_sku=p.official_sku")
        if q.keyword:
            term = f"%{' '.join(q.keyword.lower().split())}%"
            clauses.append("(lower(p.official_sku) LIKE ? OR lower(COALESCE(p.name_es,'')) LIKE ? OR lower(COALESCE(p.name_zh,'')) LIKE ? OR lower(COALESCE(es.name,'')) LIKE ? OR lower(COALESCE(zh.name,'')) LIKE ?)")
            args.extend([term] * 5)
        canonical_ids = q.canonical_ids or ((q.canonical_id,) if q.canonical_id else ())
        self._in_clause(clauses, args, "p.canonical_id", tuple(str(v) for v in canonical_ids))
        self._in_clause(clauses, args, "p.official_sku", q.skus)
        statuses = tuple(self.STATUS_MAP.get(str(s).upper(), str(s).upper()) for s in q.statuses)
        direct_statuses = tuple(s for s in statuses if s != "REAPPEARED")
        status_alternatives: list[str] = []
        if direct_statuses:
            placeholders = ",".join("?" for _ in direct_statuses)
            status_alternatives.append(f"p.status IN ({placeholders})")
            args.extend(direct_statuses)
        if "REAPPEARED" in statuses:
            status_alternatives.append("EXISTS (SELECT 1 FROM event_history ev_re WHERE ev_re.official_sku=p.official_sku AND ev_re.event_type='REAPPEARED')")
        if status_alternatives:
            clauses.append("(" + " OR ".join(status_alternatives) + ")")
        self._in_clause(clauses, args, "COALESCE(zh.cat1,es.cat1,'')", q.cat1)
        self._in_clause(clauses, args, "COALESCE(zh.cat2,es.cat2,'')", q.cat2)
        if q.min_price is not None: clauses.append("p.current_price >= ?"); args.append(q.min_price)
        if q.max_price is not None: clauses.append("p.current_price <= ?"); args.append(q.max_price)
        if q.has_original_price is True: clauses.append("p.original_price IS NOT NULL AND p.original_price > p.current_price")
        elif q.has_original_price is False: clauses.append("(p.original_price IS NULL OR p.original_price <= p.current_price)")
        for field, value in (("p.promotion_active", q.promotion), ("p.action_new_badge", q.new_badge), ("p.sustainable_badge", q.sustainable)):
            if value is not None: clauses.append(f"{field}=?"); args.append(int(value))
        if q.image_statuses: self._in_clause(clauses, args, "COALESCE(ia.status,'NO_SOURCE_URL')", tuple(str(v).upper() for v in q.image_statuses))
        if q.has_image is True: clauses.append("ia.status='AVAILABLE'")
        elif q.has_image is False: clauses.append("(ia.status IS NULL OR ia.status<>'AVAILABLE')")
        if q.image_ready_for_export is True:
            clauses.append("ia.status='AVAILABLE' AND ia.master_image_path IS NOT NULL")
        elif q.image_ready_for_export is False:
            clauses.append("(ia.status IS NULL OR ia.status<>'AVAILABLE' OR ia.master_image_path IS NULL)")
        if q.localization_status:
            status = q.localization_status.upper()
            if status == "COMPLETE":
                clauses.append("COALESCE(zh.name,'')<>'' AND COALESCE(zh.cat1,'')<>'' AND COALESCE(zh.cat2,'')<>'' AND COALESCE(zh.spec,'')<>'' AND COALESCE(zh.description,'')<>'' AND COALESCE(zh.details,'')<>''")
            elif status in {"INCOMPLETE", "STALE", "PENDING", "REVIEW_PENDING", "BLOCKED"}:
                if status == "INCOMPLETE": clauses.append("(COALESCE(zh.name,'')='' OR COALESCE(zh.cat1,'')='' OR COALESCE(zh.cat2,'')='' OR COALESCE(zh.spec,'')='' OR COALESCE(zh.description,'')='' OR COALESCE(zh.details,'')='')")
                else: clauses.append("COALESCE(zh.review_status,'')=?"); args.append("PENDING" if status == "REVIEW_PENDING" else status)
            else:
                raise ExtractionError(f"LOCALIZATION_STATUS_UNSUPPORTED: {q.localization_status}")
        for field in q.missing_fields:
            column = {
                "category": "COALESCE(zh.cat1,es.cat1)", "spec": "COALESCE(zh.spec,es.spec)",
                "description": "COALESCE(zh.description,es.description)", "image": "ia.status",
                "localization": "zh.name", "name_zh": "zh.name", "cat1_zh": "zh.cat1",
                "cat2_zh": "zh.cat2", "spec_zh": "zh.spec", "desc_zh": "zh.description",
                "details_zh": "zh.details",
            }.get(str(field).lower())
            if not column: raise ExtractionError(f"MISSING_FIELD_UNSUPPORTED: {field}")
            clauses.append(f"({column} IS NULL OR trim({column})='')")
        if q.price_change: clauses.append("ph.change_direction=?"); args.append(q.price_change.upper())
        if q.min_change_amount is not None: clauses.append("abs(COALESCE(ph.change_amount,0)) >= ?"); args.append(q.min_change_amount)
        if q.min_change_percent is not None: clauses.append("abs(COALESCE(ph.change_percent,0)) >= ?"); args.append(q.min_change_percent)
        if q.historical_low_min is not None: clauses.append("COALESCE(ps.historical_low,p.current_price) >= ?"); args.append(q.historical_low_min)
        if q.historical_low_max is not None: clauses.append("COALESCE(ps.historical_low,p.current_price) <= ?"); args.append(q.historical_low_max)
        if q.historical_high_min is not None: clauses.append("COALESCE(ps.historical_high,p.current_price) >= ?"); args.append(q.historical_high_min)
        if q.historical_high_max is not None: clauses.append("COALESCE(ps.historical_high,p.current_price) <= ?"); args.append(q.historical_high_max)
        # Backward-compatible aliases: date_from/date_to/last_n_days retain
        # their original last-seen meaning. New callers should use the
        # explicit first_seen_*, last_seen_* and event_* fields below.
        last_seen_from = q.last_seen_from or q.date_from
        last_seen_to = q.last_seen_to or q.date_to
        if q.first_seen_from: clauses.append("p.first_seen_at >= ?"); args.append(q.first_seen_from)
        if q.first_seen_to: clauses.append("p.first_seen_at <= ?"); args.append(q.first_seen_to)
        if last_seen_from: clauses.append("COALESCE(p.last_seen_at,p.last_checked_at) >= ?"); args.append(last_seen_from)
        if last_seen_to: clauses.append("COALESCE(p.last_seen_at,p.last_checked_at) <= ?"); args.append(last_seen_to)
        if q.last_n_days is not None:
            since = (datetime.now(timezone.utc) - timedelta(days=int(q.last_n_days))).date().isoformat(); clauses.append("COALESCE(p.last_seen_at,p.last_checked_at) >= ?"); args.append(since)
        # All event predicates are evaluated against the same event row. This
        # prevents a recent event of one type from satisfying a query for an
        # older event of another type.
        if q.event_types or q.event_from or q.event_to or q.event_last_n_days is not None:
            event_since = ((datetime.now(timezone.utc) - timedelta(days=int(q.event_last_n_days))).isoformat()
                           if q.event_last_n_days is not None else None)
            requested_events = tuple(str(v).upper() for v in q.event_types)

            def _date_parts(alias: str) -> tuple[list[str], list[Any]]:
                parts: list[str] = []; values: list[Any] = []
                if q.event_from: parts.append(f"{alias} >= ?"); values.append(q.event_from)
                if q.event_to: parts.append(f"{alias} <= ?"); values.append(q.event_to)
                if event_since: parts.append(f"{alias} >= ?"); values.append(event_since)
                return parts, values

            event_alternatives: list[tuple[str, list[Any]]] = []
            if not requested_events:
                dates, values = _date_parts("ev.occurred_at")
                event_alternatives.append(("EXISTS (SELECT 1 FROM event_history ev WHERE ev.official_sku=p.official_sku" + (" AND " + " AND ".join(dates) if dates else "") + ")", values))
            for event_type in requested_events:
                if event_type in {"PRICE_DOWN", "PRICE_UP"}:
                    direction = "pe.new_price < pe.old_price" if event_type == "PRICE_DOWN" else "pe.new_price > pe.old_price"
                    dates, values = _date_parts("pe.observed_at")
                    event_alternatives.append(("EXISTS (SELECT 1 FROM price_history pe WHERE pe.official_sku=p.official_sku AND pe.old_price IS NOT NULL AND " + direction + (" AND " + " AND ".join(dates) if dates else "") + ")", values))
                    # Preserve compatibility with older batches that
                    # materialized derived price directions in event_history.
                    edates, evalues = _date_parts("ev.occurred_at")
                    event_alternatives.append(("EXISTS (SELECT 1 FROM event_history ev WHERE ev.official_sku=p.official_sku AND ev.event_type=?" + (" AND " + " AND ".join(edates) if edates else "") + ")", [event_type, *evalues]))
                elif event_type == "OFFLINE":
                    dates, values = _date_parts("ls.offline_date")
                    event_alternatives.append(("EXISTS (SELECT 1 FROM lifecycle_state ls WHERE ls.official_sku=p.official_sku AND ls.offline_date IS NOT NULL" + (" AND " + " AND ".join(dates) if dates else "") + ")", values))
                else:
                    mapped = ("FIRST_SEEN", "ACTION_NEW_BADGE_ON") if event_type == "NEW" else (event_type,)
                    placeholders = ",".join("?" for _ in mapped); dates, values = _date_parts("ev.occurred_at")
                    event_alternatives.append(("EXISTS (SELECT 1 FROM event_history ev WHERE ev.official_sku=p.official_sku AND ev.event_type IN (" + placeholders + ")" + (" AND " + " AND ".join(dates) if dates else "") + ")", [*mapped, *values]))
            clauses.append("(" + " OR ".join(sql for sql, _ in event_alternatives) + ")")
            for _, values in event_alternatives: args.extend(values)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        sort_expr = self.SORTS[q.sort]
        direction = "DESC" if q.descending else "ASC"
        select = """SELECT p.canonical_id,p.official_sku,p.name_es,p.current_price,p.original_price,p.status,p.product_url,p.first_seen_at,p.last_seen_at,p.action_new_badge,p.promotion_active,p.sustainable_badge,
                   es.name AS es_name,es.cat1 AS es_cat1,es.cat2 AS es_cat2,es.spec AS es_spec,es.description AS es_description,es.details AS es_details,
                   zh.name AS zh_name,zh.cat1 AS zh_cat1,zh.cat2 AS zh_cat2,zh.spec AS zh_spec,zh.description AS zh_description,zh.details AS zh_details,zh.review_status AS zh_review_status,
                   ia.status AS image_status, ia.master_image_path, ia.width AS image_width, ia.height AS image_height,
                   ph.change_direction,ph.change_amount,ph.change_percent, ps.historical_low,ps.historical_high,
                   ev_latest.recent_event_type,ev_latest.recent_event_at"""
        joins.append("LEFT JOIN (SELECT official_sku, MIN(new_price) AS historical_low, MAX(new_price) AS historical_high FROM price_history GROUP BY official_sku) ps ON ps.official_sku=p.official_sku")
        base = f" FROM products p {' '.join(joins)}{where}"
        db_context = nullcontext(connection) if connection is not None else connect(self.db_path)
        with db_context as db:
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
        fields_complete = all(str(data.get(k) or "").strip() for k in ("zh_name", "zh_cat1", "zh_cat2", "zh_spec", "zh_description", "zh_details"))
        review_status = str(data.get("zh_review_status") or "").upper()
        data["localization_status"] = "COMPLETE" if fields_complete else (review_status if review_status in {"STALE", "PENDING", "BLOCKED", "REVIEW_PENDING"} else "INCOMPLETE")
        data["has_image"] = data.get("image_status") == "AVAILABLE"
        # The authoritative 250x250 derivative lives on the filesystem and is
        # validated by the image/export pipeline.  The read model can safely
        # expose readiness only when the DB has a known-good master asset;
        # export still performs the derivative-level validation.
        data["image_ready_for_export"] = bool(data.get("image_status") == "AVAILABLE" and data.get("master_image_path"))
        data["last_confirmed_at"] = data.get("last_seen_at")
        return data
