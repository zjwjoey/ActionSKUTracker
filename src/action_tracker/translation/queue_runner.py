"""Run the persisted translation queue against Qwen-MT Flash.

The first queue writer predates the source-version registry.  Queue rows still
contain a valid six-field source hash, while the current source of truth is the
Spanish ``product_localizations`` row.  This runner deliberately rebuilds the
source record from that row instead of requiring the newer registry table.

Results are staged as ``AI_CANDIDATE`` values with field-level provenance.  No
cleaning or approval is performed here; those remain separate workflow steps.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..database.connection import connect
from ..database.patches import create_patch
from ..database.provenance import sync_localization_field_provenance
from ..services.hashing import localization_source_hash


FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")
FIELD_TO_SOURCE = {
    "name": "name_es",
    "cat1": "cat1_es",
    "cat2": "cat2_es",
    "spec": "spec_es",
    "description": "desc_es",
    "details": "details_es",
}
SOURCE_COLUMNS = {
    "name_es": "name",
    "cat1_es": "cat1",
    "cat2_es": "cat2",
    "spec_es": "spec",
    "desc_es": "description",
    "details_es": "details",
}


def _requested_fields(value: Any) -> list[str]:
    """Accept both the old scalar queue format and the JSON list format."""
    raw = value
    if isinstance(value, str):
        try:
            raw = json.loads(value)
        except (TypeError, ValueError):
            raw = [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(raw, Mapping):
        raw = list(raw)
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, Iterable):
        return []
    return list(dict.fromkeys(field for field in (str(item).strip() for item in raw) if field in FIELDS))


def _source_record(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: row[column] for key, column in SOURCE_COLUMNS.items()}


def _candidate_id(queue_id: str, model: str) -> str:
    return hashlib.sha256(f"{queue_id}|qwen-mt-flash-v1|{model}".encode("utf-8")).hexdigest()


def _claim(db: sqlite3.Connection, rows: list[sqlite3.Row], run_id: str, now: str) -> None:
    for row in rows:
        db.execute(
            "UPDATE translation_queue SET status='IN_PROGRESS',claimed_at=?,run_id=? "
            "WHERE queue_id=? AND status='PENDING'",
            (now, run_id, row["queue_id"]),
        )


def _recover_stale_claims(db: sqlite3.Connection, now: datetime) -> int:
    """Return abandoned claims to PENDING after a bounded lease."""
    cutoff = (now - timedelta(minutes=5)).isoformat()
    cursor = db.execute(
        "UPDATE translation_queue SET status='PENDING',claimed_at=NULL,last_error=? "
        "WHERE status='IN_PROGRESS' AND (claimed_at IS NULL OR claimed_at<?)",
        ("RECOVERED_STALE_CLAIM", cutoff),
    )
    return int(cursor.rowcount or 0)


def run_qwen_translation_queue(
    db_path: Path,
    provider: Any,
    *,
    limit: int = 10,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Translate up to ``limit`` pending queue rows and stage the results."""
    run_id = run_id or f"qwen-mt-queue-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    started = datetime.now(timezone.utc)
    now = started.isoformat()
    report: dict[str, Any] = {
        "run_id": run_id,
        "claimed": 0,
        "completed": 0,
        "retried": 0,
        "blocked": 0,
        "field_calls": 0,
        "field_successes": 0,
        "field_failures": 0,
        "source_hash_reconciled": 0,
        "skus": [],
    }

    with connect(db_path) as db:
        report["recovered_stale_claims"] = _recover_stale_claims(db, started)
        db.commit()
        rows = db.execute(
            """
            SELECT q.*, p.canonical_id,
                   es.name AS name_es, es.cat1 AS cat1_es, es.cat2 AS cat2_es,
                   es.spec AS spec_es, es.description AS desc_es, es.details AS details_es,
                   zh.name AS name, zh.cat1 AS cat1, zh.cat2 AS cat2, zh.spec AS spec,
                   zh.description AS description, zh.details AS details
            FROM translation_queue q
            JOIN products p
              ON p.official_sku=q.official_sku
            JOIN product_localizations es
              ON es.official_sku=q.official_sku AND es.language='es'
            LEFT JOIN product_localizations zh
              ON zh.official_sku=q.official_sku AND zh.language='zh'
            WHERE q.status='PENDING'
            ORDER BY CASE q.priority WHEN 'HIGH' THEN 0 ELSE 1 END, q.created_at, q.queue_id
            LIMIT ?
            """,
            (max(0, int(limit)),),
        ).fetchall()
        if not rows:
            report["message"] = "NO_PENDING_ROWS"
            return report
        _claim(db, rows, run_id, now)
        db.commit()
        report["claimed"] = len(rows)

        for row in rows:
            queue_id = str(row["queue_id"])
            sku = str(row["official_sku"])
            requested = _requested_fields(row["requested_fields"])
            source = _source_record(row)
            current_hash = localization_source_hash(source)
            # Legacy queue rows use the pre-semantic aggregate hash.  The
            # source row is authoritative; retaining the queue hash only as
            # evidence avoids the old source-version-table hard failure.
            reconciled = str(row["source_hash"] or "") != current_hash
            if reconciled:
                report["source_hash_reconciled"] += 1

            translations: dict[str, str] = {}
            failures: list[dict[str, str]] = []
            for field in requested:
                text = str(source.get(FIELD_TO_SOURCE[field]) or "").strip()
                if not text:
                    failures.append({"field": field, "error": "SOURCE_FIELD_EMPTY"})
                    continue
                report["field_calls"] += 1
                result = provider.translate(text, source_locale="es", target_locale="zh-CN", field=field, sku=sku)
                translated = str(getattr(result, "text", result) or "").strip()
                error = getattr(result, "error", None)
                if translated and not error:
                    translations[field] = translated
                    report["field_successes"] += 1
                else:
                    report["field_failures"] += 1
                    failures.append({"field": field, "error": str(error or "EMPTY_TRANSLATION")[:500]})

            with db:
                if translations:
                    old = {field: row[field] for field in FIELDS}
                    merged = {**old, **translations}
                    db.execute(
                        """UPDATE product_localizations SET name=?,cat1=?,cat2=?,spec=?,description=?,details=?,
                           source=?,review_status=?,resolution_status=?,freshness_status=?,approved_by=NULL,
                           approved_at=NULL,applied_commit_id=?,updated_at=?
                           WHERE official_sku=? AND language='zh'""",
                        (merged["name"], merged["cat1"], merged["cat2"], merged["spec"], merged["description"], merged["details"],
                         "QWEN_MT_FLASH", "AI_CANDIDATE", "AI_CANDIDATE", "CURRENT", run_id, now, sku),
                    )
                    if "name" in translations:
                        db.execute("UPDATE products SET name_zh=?,updated_at=? WHERE official_sku=?", (translations["name"], now, sku))
                    provenance = {
                        "official_sku": sku, "language": "zh", **translations,
                        **source, "source": "QWEN_MT_FLASH", "review_status": "PENDING",
                        "source_hash": current_hash, "applied_commit_id": run_id,
                        "freshness_status": "CURRENT",
                    }
                    for field in translations:
                        provenance[f"{field}_source"] = "QWEN_MT_FLASH"
                        provenance[f"{field}_review_status"] = "PENDING"
                    sync_localization_field_provenance(db, provenance, commit_id=run_id, now=now)
                    for field, value in translations.items():
                        if old[field] != value:
                            create_patch(
                                db, official_sku=sku, language="zh", field_name=field,
                                old_value=old[field], new_value=value, source_hash=current_hash,
                                reason="qwen_mt_flash_translation", created_by="QWEN_MT_FLASH",
                            )
                    model = str(getattr(provider, "model", "qwen-mt-flash"))
                    candidate_id = _candidate_id(queue_id, model)
                    db.execute(
                        """INSERT INTO translation_candidates
                           (candidate_id,queue_id,official_sku,language,source_hash,model_provider,model_name,
                            prompt_version,fields_json,confidence,validation_status,approval_status,created_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(queue_id,prompt_version,model_name) DO UPDATE SET
                            fields_json=excluded.fields_json,source_hash=excluded.source_hash,
                            validation_status=excluded.validation_status,approval_status=excluded.approval_status,
                            created_at=excluded.created_at""",
                        (candidate_id, queue_id, sku, "zh", current_hash, "qwen", model,
                         "qwen-mt-flash-v1", json.dumps(translations, ensure_ascii=False, sort_keys=True),
                         None, "UNREVIEWED", "PENDING", now),
                    )

                if failures:
                    status = "BLOCKED" if all(item["error"] == "SOURCE_FIELD_EMPTY" for item in failures) and not translations else "RETRY"
                    remaining = [item["field"] for item in failures]
                    db.execute(
                        "UPDATE translation_queue SET status=?,requested_fields=?,last_error=?,retry_count=retry_count+1 WHERE queue_id=?",
                        (status, json.dumps(remaining, ensure_ascii=False), json.dumps(failures, ensure_ascii=False), queue_id),
                    )
                    report["blocked" if status == "BLOCKED" else "retried"] += 1
                else:
                    db.execute(
                        "UPDATE translation_queue SET status='COMPLETED',requested_fields=?,last_error=NULL,completed_at=? WHERE queue_id=?",
                        (json.dumps(requested, ensure_ascii=False), now, queue_id),
                    )
                    report["completed"] += 1
            report["skus"].append({"sku": sku, "queue_id": queue_id, "translated_fields": list(translations), "failures": failures})
    return report


__all__ = ["run_qwen_translation_queue"]
