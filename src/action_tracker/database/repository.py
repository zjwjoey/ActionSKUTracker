"""Read-only SQLite V2 repository used by the PRIMARY read path.

The repository deliberately exposes the same shapes as the frozen Excel/CSV
readers so the monitor does not need a second set of lifecycle decisions.
"""
from __future__ import annotations

import json
import hashlib
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .connection import connect
from .schema import migrate
from .immutable_patches import canonical_fact_version_table, ImmutablePatchError
from ..localization.hashes import canonical_json, value_hash
from ..services.hashing import localization_source_hash, localization_field_source_hash
from ..services.listing_evidence import LISTING_CANONICAL_FIELDS
from ..services.normalization import parse_price
from ..services.price import compare_price
from ..products.badges import parse_badges


class ProductionRepositoryError(RuntimeError):
    pass


# One current-price validity projection shared with SQL extraction. The
# products table retains last-valid values; a failed current observation is
# disclosed independently and cannot be made fresh by a later Chinese patch.
# Materialize only small price maps once per selected run (SQLite 3.35+),
# never a whole report per SKU. Keep json_extract's first duplicate key.
PRICE_REVIEW_JOIN = """LEFT JOIN (
    WITH latest AS MATERIALIZED (
        SELECT official_sku,run_id FROM (
            SELECT o.official_sku,o.run_id,
               ROW_NUMBER() OVER (PARTITION BY o.official_sku
                   ORDER BY o.observation_date DESC,c.committed_at DESC,c.commit_id DESC) AS rn
            FROM observations o JOIN commit_batches c ON c.run_id=o.run_id AND c.status='COMMITTED'
            WHERE o.presence_state='PRESENT'
        ) observed WHERE rn=1
    ), price_maps AS MATERIALIZED (
        SELECT e.run_id,
            CASE WHEN json_valid(e.evidence_json) THEN
                CASE WHEN json_type(e.evidence_json,'$.price_observation_invalid')='object'
                     THEN json_extract(e.evidence_json,'$.price_observation_invalid') ELSE '{}' END
                ELSE '{}' END AS prices,
            CASE WHEN json_valid(e.evidence_json) THEN
                CASE WHEN json_type(e.evidence_json,'$.price_field_observation_invalid')='object'
                     THEN json_extract(e.evidence_json,'$.price_field_observation_invalid') ELSE '{}' END
                ELSE '{}' END AS fields
        FROM run_evidence e JOIN (SELECT DISTINCT run_id FROM latest) r ON r.run_id=e.run_id
    ), flag_rows AS (
        SELECT m.run_id,j.key AS official_sku,0 AS kind,j.id AS position,j.value AS invalid_price,
               NULL AS invalid_original,NULL AS invalid_unit
        FROM price_maps m,json_each(m.prices) j
        UNION ALL
        SELECT m.run_id,j.key,1,j.id,NULL,
               CASE WHEN j.type='object' THEN json_extract(j.value,'$.original_price') END,
               CASE WHEN j.type='object' THEN json_extract(j.value,'$.unit_price') END
        FROM price_maps m,json_each(m.fields) j
    ), first_flags AS (
        SELECT * FROM (
            SELECT *,ROW_NUMBER() OVER (PARTITION BY run_id,official_sku,kind ORDER BY position) AS first_key
            FROM flag_rows
        ) WHERE first_key=1
    ), flags AS (
        SELECT run_id,official_sku,MAX(invalid_price) AS invalid_price,
               MAX(invalid_original) AS invalid_original,MAX(invalid_unit) AS invalid_unit
        FROM first_flags GROUP BY run_id,official_sku
    )
    SELECT latest.official_sku,flags.invalid_price,flags.invalid_original,flags.invalid_unit
    FROM latest LEFT JOIN flags ON flags.run_id=latest.run_id AND flags.official_sku=latest.official_sku
) price_review ON price_review.official_sku=p.official_sku"""


def current_price_expression(column: str) -> str:
    if column not in {'current_price', 'original_price', 'unit_price_raw'}:
        raise ValueError('CURRENT_PRICE_COLUMN_INVALID')
    extra = {'original_price': ' OR price_review.invalid_original=1',
             'unit_price_raw': ' OR price_review.invalid_unit=1'}.get(column, '')
    return f'CASE WHEN price_review.invalid_price=1{extra} THEN NULL ELSE p.{column} END'


def import_baseline(path: Path, records: dict[str, dict[str, Any]], observed_at: str) -> int:
    """Backward-compatible V1 baseline helper used by early migration tooling.

    It is intentionally idempotent and only seeds the legacy ``products``
    table.  New production migrations should use
    :func:`import_legacy_baseline_v2` instead.
    """
    migrate(Path(path))
    now = datetime.now().isoformat(timespec="seconds")
    with connect(Path(path)) as db:
        for sku, record in records.items():
            sku = str(sku).strip()
            if not sku:
                continue
            cid = str(record.get("canonical_id") or f"ACT{sku.zfill(7)}")
            db.execute(
                """INSERT INTO products(canonical_id,official_sku,name_es,name_zh,current_price,original_price,unit_price_raw,raw_badges,status,consecutive_missing,product_url,image_url,first_seen_at,last_seen_at,last_checked_at,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?, 'ACTIVE',0,?,?,?,?,?,?,?)
                ON CONFLICT(canonical_id) DO UPDATE SET name_es=excluded.name_es,name_zh=excluded.name_zh,current_price=excluded.current_price,original_price=excluded.original_price,unit_price_raw=excluded.unit_price_raw,raw_badges=excluded.raw_badges,status='ACTIVE',consecutive_missing=0,product_url=excluded.product_url,image_url=excluded.image_url,last_seen_at=excluded.last_seen_at,last_checked_at=excluded.last_checked_at,updated_at=excluded.updated_at""",
                (cid, sku, record.get("name_es"), record.get("name_zh"), record.get("current_price"),
                 record.get("original_price"), record.get("unit_price") or record.get("unit_price_raw"),
                 record.get("raw_tags") or record.get("raw_badges"), record.get("product_url"), record.get("image_url"),
                 record.get("first_seen") or observed_at, observed_at, observed_at, now, now),
            )
            db.execute(
                """INSERT OR IGNORE INTO product_observations(run_id,observation_date,canonical_id,official_sku,sitemap_seen,listing_seen,current_price,original_price,raw_json)
                VALUES(?,?,?,?,1,1,?,?,?)""",
                (f"BASELINE_{observed_at}", observed_at, cid, sku, record.get("current_price"),
                 record.get("original_price"), json.dumps(record, ensure_ascii=False, default=str)),
            )
        db.execute(
            "INSERT OR REPLACE INTO runs(run_id,run_date,status,qa_state,dry_run,started_at,ended_at,schema_version) VALUES(?,?, 'BASELINE_IMPORTED','PASS',0,?,?, '1.0.0')",
            (f"BASELINE_{observed_at}", observed_at, now, now),
        )
    return sum(1 for sku in records if str(sku).strip())


class ProductionRepository:
    def load_detail_states(self) -> dict[str, dict[str, Any]]:
        """Read actual collection timestamps without changing export columns."""
        with connect(self.path) as db:
            return {str(r[0]): dict(zip(("detail_last_success_at", "detail_last_success_run_id", "detail_source_hash", "detail_status"), r[1:]))
                    for r in db.execute("SELECT official_sku,detail_last_success_at,detail_last_success_run_id,detail_source_hash,detail_status FROM product_detail_state")}

    def __init__(self, path: Path):
        self.path = Path(path)
        if not self.path.exists():
            raise ProductionRepositoryError("DB_MISSING")

    def _check_v2(self, db: sqlite3.Connection) -> None:
        try:
            values = {row[0]: row[1] for row in db.execute("SELECT key,value FROM schema_metadata")}
        except sqlite3.OperationalError as exc:
            raise ProductionRepositoryError("DB_V2_SCHEMA_MISSING") from exc
        if values.get("schema_family") != "ACTION_SQLITE_DATA" or values.get("schema_version") != "2.0.0":
            raise ProductionRepositoryError("DB_SCHEMA_IDENTITY_MISMATCH")

    def load_current_products(self) -> dict[str, dict[str, Any]]:
        """Return the complete CURRENT baseline used by the daily monitor.

        The PRIMARY monitor needs the same fact projection as exports.  Reading
        ``products`` alone loses the ES localization fields, which in turn
        makes complete products look like ``MISSING_FIELD`` candidates.
        """
        return {
            str(record["sku"]): record
            for record in self.load_current_export_records()
            if str(record.get("sku") or "").strip()
        }

    def load_product_baseline(self) -> dict[str, dict[str, Any]]:
        """Historical facts for merging, never the current Presence universe."""
        return {str(row["sku"]): row for row in
                self.load_current_export_records(include_non_current=True, include_field_lineage=True)}

    def load_current_product_rows(self) -> dict[str, dict[str, Any]]:
        """Legacy products-table-only projection, retained for diagnostics."""
        with connect(self.path) as db:
            self._check_v2(db)
            rows = db.execute(
                "SELECT * FROM products WHERE status='CURRENT' ORDER BY official_sku"
            ).fetchall()
            provenance_rows = _read_field_provenance(db)
            columns = [column[1] for column in db.execute("PRAGMA table_info(products)").fetchall()]
        return {_sku(row, columns): _product_row(row, columns) for row in rows if _sku(row, columns)}

    def load_known_skus(self) -> dict[str, dict[str, Any]]:
        with connect(self.path) as db:
            self._check_v2(db)
            rows = db.execute("SELECT * FROM lifecycle_state ORDER BY official_sku").fetchall()
            columns = [column[1] for column in db.execute("PRAGMA table_info(lifecycle_state)").fetchall()]
        return {_sku(row, columns): _lifecycle_row(row, columns) for row in rows if _sku(row, columns)}

    def load_offline_skus(self) -> dict[str, dict[str, Any]]:
        known = self.load_known_skus()
        return {sku: {
            "canonical_id": record.get("canonical_id"), "official_sku": sku,
            "offline_date": record.get("offline_date"), "last_seen_date": record.get("last_seen_date"),
            "last_status": "OFFLINE",
        } for sku, record in known.items() if record.get("last_status") == "OFFLINE"}

    def current_head(self) -> str | None:
        with connect(self.path) as db:
            self._check_v2(db)
            row = db.execute("SELECT commit_id FROM commit_batches WHERE status='COMMITTED' ORDER BY committed_at DESC LIMIT 1").fetchone()
        return str(row[0]) if row else None

    def latest_commit_info(self) -> dict[str, Any] | None:
        with connect(self.path) as db:
            self._check_v2(db)
            row = db.execute(
                "SELECT c.commit_id,c.run_id,r.run_date,c.committed_at,c.status FROM commit_batches c "
                "LEFT JOIN runs r ON r.run_id=c.run_id "
                "WHERE c.status='COMMITTED' ORDER BY c.committed_at DESC LIMIT 1"
            ).fetchone()
            return _commit_info_row(db, row) if row else None

    def commit_info(self, run_id: str) -> dict[str, Any] | None:
        with connect(self.path) as db:
            self._check_v2(db)
            row = db.execute(
                "SELECT c.commit_id,c.run_id,r.run_date,c.committed_at,c.status FROM commit_batches c "
                "LEFT JOIN runs r ON r.run_id=c.run_id WHERE c.run_id=?", (run_id,)
            ).fetchone()
            return _commit_info_row(db, row) if row else None

    def latest_observation_commit_info(self) -> dict[str, Any] | None:
        """Read the latest authoritative collection, separately from repair heads."""
        with connect(self.path) as db:
            self._check_v2(db)
            return _complete_presence_head(db)

    def load_localization_source_records(self, official_skus: Iterable[str], *,
                                         include_non_current: bool = False) -> list[dict[str, Any]]:
        """Read exact Registry source identities/ES fields, without export work.

        Registry does not consume prices, Chinese approvals or Presence history.
        A required SKU set keeps a historical one-field retry from reading those
        global projections. Empty scope means no records, never all products.
        """
        if official_skus is None or isinstance(official_skus, (str, bytes)):
            raise ProductionRepositoryError('LOCALIZATION_SOURCE_SKUS_INVALID')
        selected = tuple(official_skus)
        if any(not isinstance(sku, str) or not sku or sku != sku.strip() for sku in selected):
            raise ProductionRepositoryError('LOCALIZATION_SOURCE_SKUS_INVALID')
        selected = tuple(sorted(set(selected)))
        if not selected:
            return []
        with closing(sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            self._check_v2(db)
            rows = db.execute(f'''SELECT p.canonical_id,p.official_sku,p.name_es,
                es.name,es.cat1,es.cat2,es.spec,es.description,es.details,
                p.product_url,p.image_url,p.status
                FROM products p LEFT JOIN product_localizations es
                  ON es.official_sku=p.official_sku AND es.language='es'
                WHERE p.official_sku IN (SELECT value FROM json_each(?))
                  {'' if include_non_current else "AND p.status='CURRENT'"}
                ORDER BY p.official_sku''', (json.dumps(selected),)).fetchall()
        return [{'canonical_id': row[0], 'sku': row[1],
                 **_localization_source_values(row[2], row[3:9]),
                 'product_url': row[9], 'image_url': row[10], 'status': row[11]}
                for row in rows]

    def load_current_export_records(self, *, include_non_current: bool = False,
                                    include_field_lineage: bool = False) -> list[dict[str, Any]]:
        """Return facts plus both language localization projections.

        Normal callers receive only CURRENT products. Historical export may
        request all statuses as a read-only localization reference; its own
        immutable snapshot still controls the exported SKU set and facts.
        """
        with connect(self.path) as db:
            self._check_v2(db)
            # Keep head, values and lineage in one SQLite read snapshot.
            db.execute("BEGIN")
            head_row = db.execute("SELECT commit_id,run_id FROM commit_batches WHERE status='COMMITTED' ORDER BY committed_at DESC,commit_id DESC LIMIT 1").fetchone()
            run_row = db.execute("SELECT run_id FROM runs ORDER BY started_at DESC,run_id DESC LIMIT 1").fetchone()
            status_clause = "" if include_non_current else "WHERE p.status='CURRENT'"
            rows = db.execute(
                f"""SELECT p.canonical_id,p.official_sku,p.name_es,p.name_zh,
                   {current_price_expression('current_price')},{current_price_expression('original_price')},
                   {current_price_expression('unit_price_raw')},p.raw_badges,p.action_new_badge,p.promotion_active,p.sustainable_badge,
                   p.status,p.product_url,p.image_url,p.first_seen_at,p.last_seen_at,
                   es.name,es.cat1,es.cat2,es.spec,es.description,es.details,
                   zh.name,zh.cat1,zh.cat2,zh.spec,zh.description,zh.details,
                   zh.source_hash,zh.resolution_status,zh.review_status,zh.freshness_status,
                   zh.name_source,zh.cat1_source,zh.cat2_source,zh.spec_source,
                   zh.description_source,zh.details_source,zh.approved_by,zh.approved_at,
                   zh.last_commit_id,zh.applied_commit_id,zh.unit_price,zh.unit_price_source,
                   p.current_price,p.original_price,p.unit_price_raw,price_review.invalid_price,
                   p.consecutive_missing,p.last_checked_at,price_review.invalid_unit
                   FROM products p
                   LEFT JOIN product_localizations es ON es.official_sku=p.official_sku AND es.language='es'
                   LEFT JOIN product_localizations zh ON zh.official_sku=p.official_sku AND zh.language='zh'
                   {PRICE_REVIEW_JOIN}
                   {status_clause} ORDER BY p.official_sku"""
            ).fetchall()
            provenance_rows = _read_field_provenance(db)
            price_rows = db.execute(
                "SELECT official_sku,observed_at,old_price,new_price,change_type "
                "FROM price_history ORDER BY observed_at,id"
            ).fetchall()
            presence_head = _complete_presence_head(db)
            lineage = _read_fact_lineage(db, rows) if include_field_lineage else {}
            pending_ids = {str(row[1]) for row in rows if row[11] == 'UNKNOWN' and not (row[14] or row[15])}
            facts_only_origins = set()
            if pending_ids:
                # Identity creation is verified by the writer against the DB
                # predecessor. Do not infer it from undated legacy UNKNOWNs.
                for item in db.execute("""SELECT c.run_id FROM run_evidence e
                        JOIN commit_batches c ON c.run_id=e.run_id AND c.status='COMMITTED'
                        WHERE CASE WHEN json_valid(e.evidence_json)
                            THEN json_extract(e.evidence_json,'$.commit_scope') END='POSITIVE_FACTS_ONLY'
                        ORDER BY c.committed_at DESC"""):
                    stored = db.execute('SELECT evidence_json FROM run_evidence WHERE run_id=?', (item[0],)).fetchone()
                    created = json.loads(stored[0]).get('new_identity_skus')
                    if isinstance(created, list):
                        facts_only_origins.update(sku for sku in created if isinstance(sku, str) and sku in pending_ids)
                    if facts_only_origins == pending_ids:
                        break
        price_history: dict[str, list[Any]] = {}
        for price_row in price_rows:
            price_history.setdefault(str(price_row[0]), []).append(price_row)
        period_date = str(presence_head["run_date"]) if presence_head else ""
        source_commit_id = str(head_row[0]) if head_row else ""
        source_run_id = str(run_row[0]) if run_row else ""
        records = []
        field_provenance: dict[str, dict[str, dict[str, Any]]] = {}
        for item in provenance_rows:
            field_provenance.setdefault(str(item[0]), {})[str(item[1])] = {
                "value": item[2], "source": item[3], "review_status": item[4],
                "source_hash": item[5], "updated_at": item[6], "applied_commit_id": item[7],
                "approved_by": item[8], "approved_at": item[9], "freshness_status": item[10],
            }
        for row in rows:
            es_source = _localization_source_values(row[2], row[16:22])
            records.append({
                "canonical_id": row[0], "sku": row[1], "name_es": row[16] or row[2], "name_zh": row[22] or row[3],
                "_stored_name_zh": row[3],
                "_facts_only_without_presence": str(row[1]) in facts_only_origins,
                "current_price": row[4], "original_price": row[5], "unit_price": row[6], "raw_tags": row[7],
                "last_valid_current_price": row[44], "last_valid_original_price": row[45],
                "last_valid_unit_price": row[46], "price_review_pending": row[47] == 1,
                "is_new_badge": bool(row[8]), "promotion": bool(row[9]), "sustainable": bool(row[10]),
                "status": row[11], "product_url": row[12], "image_url": row[13], "first_seen": row[14], "last_seen": row[15],
                "consecutive_missing": row[48], "last_checked_at": row[49],
                "cat1_es": row[17], "cat2_es": row[18], "spec_es": row[19], "desc_es": row[20], "details_es": row[21],
                "cat1_zh": row[23], "cat2_zh": row[24], "spec_zh": row[25], "desc_zh": row[26], "details_zh": row[27],
                "zh_source_hash": row[28], "zh_resolution_status": row[29], "zh_review_status": row[30], "zh_freshness_status": row[31],
                "zh_name_source": row[32], "zh_cat1_source": row[33], "zh_cat2_source": row[34], "zh_spec_source": row[35],
                "zh_description_source": row[36], "zh_details_source": row[37], "zh_approved_by": row[38], "zh_approved_at": row[39],
                "zh_last_commit_id": row[40], "zh_applied_commit_id": row[41],
                "unit_price_zh": None if row[47] == 1 or row[50] == 1 else row[42],
                "zh_unit_price_source": "PRICE_REVIEW_REQUIRED" if row[47] == 1 or row[50] == 1 else row[43],
                "source_commit_id": source_commit_id, "source_run_id": source_run_id,
                "baseline_head_commit_id": source_commit_id,
                "baseline_head_run_id": str(head_row[1]) if head_row else "",
                "presence_source_commit_id": (presence_head or {}).get("commit_id"),
                "presence_source_run_id": (presence_head or {}).get("run_id"),
                "presence_observation_date": (presence_head or {}).get("run_date"),
                **({"field_source_lineage": lineage.get(str(row[1]), {})}
                   if include_field_lineage else {}),
                # A Phase 3-only run reads its facts back from PRIMARY rather
                # than retaining the collector's in-memory record.  Rebuild
                # the same aggregate Spanish binding and make the committed
                # CURRENT state explicit as presence evidence, so the export
                # audit evaluates the durable source instead of a lossy read
                # projection.
                "source_hash": localization_source_hash(es_source),
                "presence_source": (f"PRIMARY_CURRENT_COMMIT:{presence_head['commit_id']}"
                                    if presence_head else ""),
                "zh_field_provenance": field_provenance.get(str(row[1]), {}),
                **_price_projection(row[4], row[5], row[7], bool(row[9]),
                                    price_history.get(str(row[1]), []), period_date,
                                    last_valid_price=row[44]),
            })
        return records


def _localization_source_values(product_name, localized_values) -> dict[str, Any]:
    """The existing ES name fallback; preserve None/empty for every other field."""
    name, cat1, cat2, spec, description, details = localized_values
    return {'name_es': name or product_name, 'cat1_es': cat1, 'cat2_es': cat2,
            'spec_es': spec, 'desc_es': description, 'details_es': details}


def _commit_info_row(db: sqlite3.Connection, row) -> dict[str, Any]:
    """Old evidence may omit scope; an explicit invalid scope never means full."""
    scope = 'FULL_OBSERVATION'
    stored = db.execute('SELECT evidence_json FROM run_evidence WHERE run_id=?', (row[1],)).fetchone()
    if stored is not None:
        try:
            evidence = json.loads(stored[0])
        except (TypeError, ValueError) as exc:
            raise ProductionRepositoryError('COMMIT_SCOPE_EVIDENCE_INVALID') from exc
        if not isinstance(evidence, dict):
            raise ProductionRepositoryError('COMMIT_SCOPE_EVIDENCE_INVALID')
        scope = evidence.get('commit_scope', 'FULL_OBSERVATION')
        if not isinstance(scope, str) or scope not in {'FULL_OBSERVATION', 'POSITIVE_FACTS_ONLY', 'HISTORICAL_FACTS_ONLY'}:
            raise ProductionRepositoryError('COMMIT_SCOPE_EVIDENCE_INVALID')
    return {'commit_id': row[0], 'run_id': row[1], 'run_date': row[2],
            'committed_at': row[3], 'status': row[4], 'commit_scope': scope}


def _complete_presence_head(db: sqlite3.Connection) -> dict[str, Any] | None:
    """Fact/repair heads cannot advance complete Presence authority."""
    # Sort only bounded metadata. Carrying evidence_json through this sort
    # materializes every historical report before returning its first row.
    rows = db.execute("""SELECT c.commit_id,c.run_id,r.run_date,c.committed_at,c.status
        FROM commit_batches c JOIN runs r ON r.run_id=c.run_id
        WHERE c.status='COMMITTED' AND c.observation_count>0 AND r.dry_run=0
          AND r.qa_state IN ('PASS','PASS_PRESENCE_ONLY')
          AND EXISTS (SELECT 1 FROM observations o WHERE o.run_id=c.run_id
                      AND o.observation_complete=1)
        ORDER BY c.committed_at DESC,c.commit_id DESC""")
    for row in rows:
        info = _commit_info_row(db, row)
        if info['commit_scope'] == 'FULL_OBSERVATION':
            return info
    return None


def _read_fact_lineage(db: sqlite3.Connection, rows) -> dict[str, dict[str, dict[str, Any]]]:
    """Resolve matching committed source facts once, without copying history.

    This is baseline-only evidence. Ordinary exports do not scan fact history.
    A retained snapshot and a metadata commit cannot become a fresh source.
    """
    mapping = {"name": ("name_es", 16), "cat1": ("cat1_es", 17),
               "cat2": ("cat2_es", 18), "spec": ("spec_es", 19),
               "description": ("desc_es", 20), "details": ("details_es", 21)}
    expected = {}
    output = {}
    for row in rows:
        sku = str(row[1])
        expected[sku] = {key: row[index] if index != 16 else row[16] or row[2]
                         for key, index in mapping.values()}
        expected[sku]["current_price"] = row[44]
        output[sku] = {field: {"status": "UNKNOWN", "reason": "NO_MATCHING_TRUSTED_SOURCE"}
                       for field in (*expected[sku], "original_price", "unit_price", "product_url", "image_url")}
    # Version/observation rows are ordered by run. Keep only one decoded
    # report, instead of joining its full JSON into every sorted fact row.
    evidence_run = object()
    evidence_cache = None

    def run_evidence(run_id):
        nonlocal evidence_run, evidence_cache
        if run_id != evidence_run:
            evidence_run = run_id
            stored = db.execute('SELECT evidence_json FROM run_evidence WHERE run_id=?', (run_id,)).fetchone()
            try:
                decoded = json.loads((stored[0] if stored else None) or '{}')
                evidence_cache = decoded if isinstance(decoded, dict) else None
            except (ValueError, TypeError):
                evidence_cache = None
        return evidence_cache
    try:
        table = canonical_fact_version_table(db)
    except ImmutablePatchError:
        table = None
    if table:
        cursor = db.execute(f"""SELECT f.*,c.commit_id,r.run_date,
            (SELECT o.observation_date FROM observations o
             WHERE o.run_id=f.run_id AND o.official_sku=f.official_sku) AS source_day
            FROM {table} f JOIN commit_batches c ON c.run_id=f.run_id AND c.status='COMMITTED'
            JOIN runs r ON r.run_id=f.run_id
            ORDER BY c.committed_at DESC,c.commit_id DESC,f.fact_id DESC""")
        for item in cursor:
            sku = str(item["official_sku"])
            if sku not in expected:
                continue
            try:
                evidence = run_evidence(item['run_id'])
                if evidence is None:
                    continue
                states = (evidence.get("fact_field_provenance") or {}).get(sku) or {}
                if table == "product_fact_versions":
                    raw = json.loads(item["raw_fact_json"])
                    normalized = json.loads(item["normalized_fact_json"])
                    if any(hashlib.sha256(canonical_json(payload).encode()).hexdigest() != item[column]
                           for payload, column in ((raw, "raw_fact_hash"), (normalized, "normalized_fact_hash"))):
                        continue
                    fields = mapping
                else:
                    fields = {item["field_name"]: mapping[item["field_name"]]} if item["field_name"] in mapping else {}
                    raw = {item["field_name"]: item["raw_value"]}
                    normalized = {item["field_name"]: item["normalized_value"]}
                    if value_hash(item["raw_value"]) != item["raw_hash"] or value_hash(item["normalized_value"]) != item["normalized_hash"]:
                        continue
                for field, (key, _) in fields.items():
                    if output[sku][key]["status"] != "UNKNOWN" or expected[sku][key] in (None, ""):
                        continue
                    state = (states.get(key) or {}).get("state")
                    if state not in (None, "CURRENT_VERIFIED") or raw.get(field) in (None, ""):
                        continue
                    if state is None:
                        # Old writers copied merged baseline values into raw
                        # JSON. A Listing source never observed detail-only
                        # fields, even when the copied whole-record hash fits.
                        layer = item["normalization_version"] if table == "product_fact_versions" else item["source_name"]
                        if layer != "detail" and not (layer == "listing" and key in LISTING_CANONICAL_FIELDS):
                            continue
                    if any(issue.get("level") in {"FIELD_REVIEW", "HARD_BLOCK"}
                           and str(issue.get("sku")) == sku and issue.get("field_name") == key
                           for issue in evidence.get("qa_issues") or []):
                        continue
                    if value_hash(normalized.get(field)) != value_hash(expected[sku][key]):
                        continue
                    observed = item["observed_at"] if table == "source_fact_versions" else item["source_day"]
                    output[sku][key] = {
                        "status": "VERIFIED", "source_table": table,
                        "field_source_version": item["fact_id"], "field_source_run_id": item["run_id"],
                        "field_source_commit_id": item["commit_id"], "field_source_observed_at": observed,
                        "field_source_time_precision": "DECLARED" if table == "source_fact_versions" and observed else ("DAY" if observed else "UNKNOWN"),
                        "field_source_business_date": item["run_date"],
                        "field_raw_hash": value_hash(raw[field]),
                        "field_value_hash": value_hash(normalized.get(field)),
                        "source_snapshot_hash": item["normalized_fact_hash"] if table == "product_fact_versions" else None}
            except (ValueError, TypeError, AttributeError):
                # Malformed historical evidence is unknown, never invented.
                continue
    # Recovery already has a committed, field-bound evidence contract. It
    # does not necessarily create a fact-version row, and its commit date is
    # not the original artifact observation date.
    for item in db.execute("""SELECT e.run_id,c.commit_id,r.run_date
            FROM run_evidence e JOIN commit_batches c ON c.run_id=e.run_id
            JOIN runs r ON r.run_id=e.run_id WHERE c.status='COMMITTED'
            AND CASE WHEN json_valid(e.evidence_json) THEN json_extract(e.evidence_json,'$.operation') END
                IN ('HISTORICAL_SOURCE_RECOVERY','HISTORICAL_NORMALIZATION_CORRECTION','HISTORICAL_FACT_IMPORT')
            ORDER BY c.committed_at DESC,c.commit_id DESC"""):
        try:
            evidence = run_evidence(item['run_id'])
            if evidence is None:
                continue
            correction = evidence["operation"] == "HISTORICAL_NORMALIZATION_CORRECTION"
            for fact in evidence.get("field_evidence") or []:
                sku, field = str(fact.get("sku") or ""), fact.get("field")
                if sku not in expected or field not in mapping:
                    continue
                key = mapping[field][0]
                if output[sku][key]["status"] != "UNKNOWN" or expected[sku][key] in (None, ""):
                    continue
                if (correction and field != "details") or (not correction and fact.get("status") != "VERIFIED"):
                    continue
                normalized = fact.get("after") if correction else fact.get("normalized")
                raw, source = fact.get("raw"), fact.get("source") or {}
                if (not raw or raw != source.get("text") or not source.get("reference")
                        or len(str(source.get("file_hash") or "")) != 64
                        or source.get("field_hash") != localization_field_source_hash({key: raw}, field)
                        or value_hash(normalized) != value_hash(expected[sku][key])):
                    continue
                observed = source.get("observed_at")
                try:
                    datetime.fromisoformat(str(observed).replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    observed = None
                output[sku][key] = {"status": "VERIFIED", "source_table": "run_evidence",
                    "field_source_version": f"{item['run_id']}:{sku}:{field}",
                    "field_source_run_id": item["run_id"], "field_source_commit_id": item["commit_id"],
                    "field_source_observed_at": observed,
                    "field_source_time_precision": ("DAY" if len(str(observed)) == 10 else "DECLARED") if observed else "UNKNOWN",
                    "field_source_business_date": (str(observed)[:10] if observed else None) if evidence['operation'] == 'HISTORICAL_FACT_IMPORT' else item["run_date"], "field_raw_hash": value_hash(raw),
                    "field_value_hash": value_hash(normalized), "source_snapshot_hash": None,
                    "source_reference": source["reference"], "source_artifact_hash": source["file_hash"],
                    "source_field_hash": source["field_hash"], "evidence_contract": evidence["operation"]}
        except (ValueError, TypeError, AttributeError, KeyError):
            continue
    for item in db.execute("""SELECT o.*,c.commit_id FROM observations o
            JOIN commit_batches c ON c.run_id=o.run_id AND c.status='COMMITTED'
            WHERE o.presence_state='PRESENT' AND o.current_price IS NOT NULL
            ORDER BY o.observation_date DESC,c.committed_at DESC,c.commit_id DESC"""):
        sku = str(item["official_sku"])
        if sku not in expected or output[sku]["current_price"]["status"] != "UNKNOWN":
            continue
        try:
            ev = run_evidence(item['run_id'])
            if ev is None:
                continue
            state = (((ev.get("fact_field_provenance") or {}).get(sku) or {}).get("current_price") or {}).get("state")
            rejected = any(i.get("level") in {"FIELD_REVIEW", "HARD_BLOCK"} and str(i.get("sku")) == sku and i.get("field_name") == "current_price" for i in ev.get("qa_issues") or [])
            if state != "CURRENT_VERIFIED" or rejected or (ev.get("price_observation_invalid") or {}).get(sku):
                continue
            if value_hash(item["current_price"]) != value_hash(expected[sku]["current_price"]):
                continue
            output[sku]["current_price"] = {"status": "VERIFIED", "source_table": "observations",
                "field_source_version": f"{item['run_id']}:{sku}", "field_source_run_id": item["run_id"],
                "field_source_commit_id": item["commit_id"], "field_source_observed_at": item["observation_date"],
                "field_source_time_precision": "DAY", "field_value_hash": value_hash(item["current_price"])}
        except (ValueError, TypeError, AttributeError):
            continue
    return output


def _price_projection(current, original, raw_badges, promotion, history, period_date, *, last_valid_price=None):
    """Recompute compatibility prices from PRIMARY, never old Excel cells.

    Last-change columns describe the most recent real change; price_change
    describes the committed business date. Ratios use Excel's fractional
    percent convention. Include old prices and the current price in extrema.
    """
    current_price = parse_price(current)
    valid = [p for item in history for p in (parse_price(item[2]), parse_price(item[3]))
             if p is not None and p > 0]
    if current_price is not None and current_price > 0:
        valid.append(current_price)
    historical_price = parse_price(last_valid_price)
    if historical_price is not None and historical_price > 0:
        valid.append(historical_price)
    changes = [item for item in history
               if parse_price(item[2]) is not None and parse_price(item[2]) > 0
               and parse_price(item[3]) is not None and parse_price(item[3]) > 0
               and compare_price(parse_price(item[2]), parse_price(item[3])).change_type in ('UP', 'DOWN')]
    latest = changes[-1] if changes else None
    pc = compare_price(parse_price(latest[2]), parse_price(latest[3])) if latest else None
    period = [item for item in history if str(item[1])[:10] == period_date]
    period_change = ('NEW' if parse_price(period[-1][2]) is None else
                     compare_price(parse_price(period[-1][2]), parse_price(period[-1][3])).change_type) if period else 'UNCHANGED'
    discount = parse_badges(raw_badges).discount if promotion else None
    if discount is not None and not -1 < discount < 0:
        discount = None
    original_price = parse_price(original)
    if discount is None and original_price is not None and current_price is not None and original_price > current_price > 0:
        discount = round((current_price - original_price) / original_price, 6)
    return {
        'last_price': parse_price(latest[2]) if latest else None,
        'price_direction': pc.change_type if pc else None,
        'price_change': period_change,
        'change_amount': pc.amount if pc else None,
        'change_percent': pc.percent if pc else None,
        'last_change_date': str(latest[1])[:10] if latest else None,
        'price_min': min(valid) if valid else None,
        'price_max': max(valid) if valid else None,
        'discount': discount,
    }


def _sku(row: sqlite3.Row | tuple, columns: list[str]) -> str:
    return str(row[columns.index("official_sku")] or "").strip()


def _read_field_provenance(db: sqlite3.Connection) -> list[sqlite3.Row | tuple]:
    """Read the active canonical projection with legacy fallback.

    PRIMARY databases use ``localization_fields``.  Older closure fixtures
    used ``localization_field_provenance`` and carry three extra audit
    columns.  Returning a stable eleven-column shape keeps the repository
    read path compatible without treating the legacy table as authoritative
    when the canonical projection is populated.
    """
    try:
        rows = db.execute(
            """SELECT official_sku,field_name,value,source,review_status,source_hash,
                      updated_at,applied_commit_id,approved_by,
                      approved_at,freshness_status
               FROM localization_fields WHERE language='zh'"""
        ).fetchall()
        if rows:
            return rows
    except sqlite3.OperationalError:
        pass
    try:
        return db.execute(
            """SELECT official_sku,field_name,value,source,review_status,source_hash,
                      updated_at,applied_commit_id,approved_by,approved_at,freshness_status
               FROM localization_field_provenance WHERE language='zh'"""
        ).fetchall()
    except sqlite3.OperationalError:
        return []


def _value(row, columns: list[str], key: str, default=None):
    try:
        index = columns.index(key)
    except ValueError:
        return default
    return row[index]


def _product_row(row, columns: list[str]) -> dict[str, Any]:
    mapping = {
        "canonical_id": "canonical_id", "sku": "official_sku", "name_es": "name_es", "name_zh": "name_zh",
        "current_price": "current_price", "original_price": "original_price", "unit_price": "unit_price_raw",
        "raw_tags": "raw_badges", "is_new_badge": "action_new_badge", "promotion": "promotion_active",
        "sustainable": "sustainable_badge", "status": "status", "missing_count": "consecutive_missing",
        "product_url": "product_url", "image_url": "image_url", "first_seen": "first_seen_at", "last_seen": "last_seen_at",
    }
    result = {target: _value(row, columns, source) for target, source in mapping.items()}
    for key in ("is_new_badge", "promotion", "sustainable"):
        result[key] = bool(result.get(key))
    return result


def _lifecycle_row(row, columns: list[str]) -> dict[str, Any]:
    mapping = {
        "canonical_id": "canonical_id", "official_sku": "official_sku", "first_seen_date": "first_seen_date",
        "last_seen_date": "last_seen_date", "last_status": "current_status", "missing_count": "missing_count",
        "last_missing_date": "last_missing_date", "offline_date": "offline_date",
        "last_state_observation_date": "last_state_observation_date", "ever_offline": "ever_offline",
        "last_run_id": "last_run_id", "updated_at": "updated_at",
    }
    result = {target: _value(row, columns, source) for target, source in mapping.items()}
    result["missing_count"] = str(result.get("missing_count") or 0)
    result["ever_offline"] = "true" if result.get("ever_offline") else "false"
    return result
