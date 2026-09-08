"""Import verified detail pages collected in the Edge browser session.

The normal Playwright session may be BLOCKED while a user-controlled Edge tab
can still load a page at a low frequency.  This module is intentionally an
evidence bridge: it accepts a UTF-8 JSON export from that tab, validates every
row against a committed Presence snapshot, then updates only Spanish detail
fields in SQLite PRIMARY.  It never changes Presence, lifecycle, prices,
badges or Chinese translations.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..database.integration import database_path, regenerate_compatibility_exports
from ..database.production import ProductionDatabaseError, apply_detail_only_updates
from ..database.repository import ProductionRepository
from ..exporting.excel_writer import write_catalog_xlsx
from ..excel.reader import ES_MAP, load_current
from ..products.badges import parse_badges


class EdgeDetailImportError(ValueError):
    """Input or parent-observation validation failed."""


_CHALLENGE_RE = re.compile(
    r"just a moment|please wait|请稍候|正在进行安全验证|security verification|"
    r"verificación de seguridad|checking your browser|enable javascript|"
    r"ray id|access denied|acceso denegado",
    re.IGNORECASE,
)


def _is_challenge_page(title: str, body_sample: str) -> bool:
    """Detect an actual interstitial, not the ordinary phrase ``un momento``.

    Action product descriptions commonly contain Spanish prose such as
    ``disfruta de un momento de relajación``.  Treating every ``un momento``
    occurrence as Cloudflare incorrectly discards otherwise complete detail
    records.  A standalone challenge title is still recognized, while body
    matches require an explicit verification/access-denied signal.
    """
    title_text = " ".join(str(title or "").split()).strip()
    if re.fullmatch(r"(?:just a moment|un momento|please wait)[.…! ]*", title_text, re.IGNORECASE):
        return True
    return bool(_CHALLENGE_RE.search(f"{title}\n{body_sample}"))
_SKU_RE = re.compile(r"^\d+$")
_OK_STATUSES = {"OK", "SUCCESS", "COMPLETE", "COMPLETED"}
_DETAIL_FIELDS = ("name_es", "cat1_es", "cat2_es", "spec_es", "desc_es", "details_es")
# The staging data sheet is deliberately schema-identical to the Spanish
# Master CURRENT sheet.  Audit metadata stays in the JSON sidecar/evidence,
# never in a column that could be mistaken for a Master fact.
EDGE_STAGING_HEADERS = list(ES_MAP)
ES_MAP_INV = {internal_key: header for header, internal_key in ES_MAP.items()}

# Fields that identify a complete Master-compatible Spanish CURRENT row.  A
# row may still be useful as detail-only evidence when historical/optional
# values are unavailable, but it must never be presented as a complete row.
MASTER_DETAIL_MERGE_HEADERS = (
    "Canonical_ID", "SKU", "西班牙语品名", "当前售价 (€)",
    "一级类目（西语）", "规格（西语）", "当前状态",
    "首次发现日期", "最后确认存在日期", "描述（西语）", "产品详情（西语）",
    "商品链接", "匹配状态",
)
_DATE_HEADERS = {"最近变价日期", "首次发现日期", "最后确认存在日期"}


def _excel_date(value: Any) -> date | None:
    """Convert an ISO date-like value to a real Excel date cell value."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise EdgeDetailImportError(f"EDGE_STAGING_DATE_INVALID:{value}") from exc


def _validate_master_es_schema(path: Path) -> None:
    """Fail closed if the on-disk Master ES sheet drifts from ES_MAP."""
    import openpyxl

    try:
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            if "02_SKU_ES_CURRENT" not in workbook.sheetnames:
                raise EdgeDetailImportError("EDGE_STAGING_MASTER_SHEET_MISSING:02_SKU_ES_CURRENT")
            headers = [cell.value for cell in next(workbook["02_SKU_ES_CURRENT"].iter_rows(max_row=1))]
        finally:
            workbook.close()
    except EdgeDetailImportError:
        raise
    except (OSError, ValueError) as exc:
        raise EdgeDetailImportError("EDGE_STAGING_MASTER_SCHEMA_READ_FAILED") from exc
    if headers != EDGE_STAGING_HEADERS:
        raise EdgeDetailImportError("EDGE_STAGING_MASTER_SCHEMA_MISMATCH")


def _read_payload(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EdgeDetailImportError(f"EDGE_IMPORT_JSON_INVALID:{path}") from exc
    if isinstance(payload, list):
        return {}, [dict(row) for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        raise EdgeDetailImportError("EDGE_IMPORT_PAYLOAD_NOT_OBJECT")
    records = payload.get("records") or payload.get("details") or []
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        raise EdgeDetailImportError("EDGE_IMPORT_RECORDS_INVALID")
    return payload, [dict(row) for row in records]


def _clean_text(value: Any, *, field: str, sku: str) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise EdgeDetailImportError(f"EDGE_IMPORT_FIELD_NOT_TEXT:{sku}:{field}")
    if "\ufffd" in value or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise EdgeDetailImportError(f"EDGE_IMPORT_ENCODING_ERROR:{sku}:{field}")
    if any(ord(char) < 32 and char not in "\r\n\t" for char in value):
        raise EdgeDetailImportError(f"EDGE_IMPORT_CONTROL_CHARACTER:{sku}:{field}")
    return value.strip()


def _normalize_description(value: Any) -> str | None:
    """Remove the page section heading from captured description content."""
    text = _clean_text(value, field="desc_es", sku="staging")
    if not text:
        return None
    return re.sub(r"^\s*descripci[oó]n\s*\n", "", text, count=1, flags=re.IGNORECASE).strip()


def _validate_url(url: Any, sku: str) -> str:
    value = _clean_text(url, field="product_url", sku=sku)
    if not value:
        raise EdgeDetailImportError(f"EDGE_IMPORT_URL_MISSING:{sku}")
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname not in {"action.com", "www.action.com"}:
        raise EdgeDetailImportError(f"EDGE_IMPORT_URL_HOST_INVALID:{sku}")
    if not re.search(rf"/es-es/p/{re.escape(sku)}(?:/|$)", parsed.path):
        raise EdgeDetailImportError(f"EDGE_IMPORT_URL_SKU_MISMATCH:{sku}")
    return value


def _blocked_parent(parent: Path) -> bool:
    try:
        report = json.loads((parent / "run_report.json").read_text(encoding="utf-8"))
        if report.get("final_access_state") == "BLOCKED" or report.get("detail_access_state") == "BLOCKED":
            return True
    except (OSError, UnicodeError, json.JSONDecodeError):
        pass
    reports = parent.glob("detail_retries/*/detail_retry_report.json")
    return any(json.loads(path.read_text(encoding="utf-8")).get("final_access_state") == "BLOCKED" for path in reports)


def _validate_parent(cfg: dict[str, Any], run_id: str, *, for_staging: bool = False) -> tuple[Path, set[str]]:
    matches = list(Path(cfg["paths"]["snapshots"]).glob(f"*/{run_id}"))
    if len(matches) != 1:
        raise EdgeDetailImportError(f"EDGE_IMPORT_PARENT_NOT_FOUND:{run_id}")
    parent = matches[0]
    try:
        report = json.loads((parent / "run_report.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EdgeDetailImportError("EDGE_IMPORT_PARENT_REPORT_INVALID") from exc
    valid_staging_parent = (
        report.get("qa_state") == "PASS"
        and report.get("commit_status") in {"FULL_COMMIT", "DRY_RUN"}
        and (for_staging or not report.get("dry_run"))
    )
    if not valid_staging_parent:
        raise EdgeDetailImportError("EDGE_IMPORT_PARENT_NOT_COMMITTED")
    # A staging-only export may be generated from a QA-passing dry-run.  It
    # never writes PRIMARY and is useful for Edge recovery planning.  The
    # actual importer remains strict and still requires a committed BLOCKED
    # parent, preventing a dry-run from becoming a write authority.
    if not for_staging and not _blocked_parent(parent):
        raise EdgeDetailImportError("EDGE_IMPORT_PARENT_NOT_BLOCKED")
    updates_path = parent / "product_updates.csv"
    import csv
    with updates_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    candidates = {
        str(row.get("sku") or "").strip()
        for row in rows
        if str(row.get("need_detail") or "").strip().lower() in {"1", "true", "yes"}
    }
    if not candidates:
        raise EdgeDetailImportError("EDGE_IMPORT_PARENT_DETAIL_QUEUE_EMPTY")
    return parent, candidates


def _validate_records(payload: dict[str, Any], records: list[dict[str, Any]], candidates: set[str],
                      current: dict[str, dict[str, Any]], *, allow_already_enriched: bool = False,
                      allow_incomplete: bool = False, skip_unverified: bool = False) -> list[dict[str, Any]]:
    if payload.get("source") not in (None, "EDGE_PLUGIN", "EDGE_BROWSER"):
        raise EdgeDetailImportError("EDGE_IMPORT_SOURCE_INVALID")
    parent_id = str(payload.get("parent_run_id") or "").strip()
    if payload.get("parent_run_id") and not parent_id:
        raise EdgeDetailImportError("EDGE_IMPORT_PARENT_ID_INVALID")
    seen: set[str] = set()
    valid: list[dict[str, Any]] = []
    for raw in records:
        sku = str(raw.get("sku") or "").strip()
        if not _SKU_RE.fullmatch(sku):
            raise EdgeDetailImportError(f"EDGE_IMPORT_SKU_INVALID:{sku}")
        if sku in seen:
            raise EdgeDetailImportError(f"EDGE_IMPORT_DUPLICATE_SKU:{sku}")
        seen.add(sku)
        if sku not in current:
            raise EdgeDetailImportError(f"EDGE_IMPORT_SKU_NOT_CURRENT:{sku}")
        # A BLOCKED recovery import may cover a SKU that was not in the
        # original capped plan, but only when the PRIMARY row still lacks
        # both detail fields.  This keeps the bridge narrow while allowing
        # user-verified Edge pages to drain a backlog from an older run.
        existing = current[sku]
        has_description = bool(existing.get("description_es") or existing.get("desc_es"))
        has_details = bool(existing.get("details_es"))
        if not allow_already_enriched and sku not in candidates and has_description and has_details:
            raise EdgeDetailImportError(f"EDGE_IMPORT_SKU_NOT_PLANNED:{sku}")
        status = str(raw.get("page_status") or raw.get("status") or "OK").strip().upper()
        title = str(raw.get("page_title") or raw.get("title") or "")
        body_sample = str(raw.get("body_sample") or "")
        if status not in _OK_STATUSES or _is_challenge_page(title, body_sample):
            if skip_unverified:
                continue
            raise EdgeDetailImportError(f"EDGE_IMPORT_PAGE_NOT_VERIFIED:{sku}")
        row: dict[str, Any] = {"sku": sku, "product_url": _validate_url(raw.get("product_url") or raw.get("url"), sku)}
        for field in _DETAIL_FIELDS:
            value = _clean_text(raw.get(field), field=field, sku=sku)
            if value is not None:
                row[field] = value
        if not allow_incomplete and (not row.get("desc_es") or not row.get("details_es")):
            raise EdgeDetailImportError(f"EDGE_IMPORT_DETAIL_CONTENT_INCOMPLETE:{sku}")
        if raw.get("image_url") is not None:
            row["image_url"] = _clean_text(raw.get("image_url"), field="image_url", sku=sku)
        row["edge_page_title"] = title
        row["edge_observed_at"] = raw.get("observed_at") or datetime.now(timezone.utc).isoformat()
        valid.append(row)
    return valid


def export_edge_detail_table(cfg: dict[str, Any], *, run_id: str, input_path: Path | list[Path],
                             output_path: Path) -> dict[str, Any]:
    """Write validated Edge records to a standalone staging workbook only.

    This path intentionally does not open a write transaction, update SQLite,
    or regenerate Master.  It is the safe hand-off while a batch is still
    under review.
    """
    _parent, candidates = _validate_parent(cfg, run_id, for_staging=True)
    paths = input_path if isinstance(input_path, list) else [input_path]
    payload: dict[str, Any] = {}
    records: list[dict[str, Any]] = []
    for path in paths:
        part_payload, part_records = _read_payload(Path(path))
        payload.update({key: value for key, value in part_payload.items() if key not in {"records", "details"}})
        payload_parent = str(part_payload.get("parent_run_id") or "").strip()
        if payload_parent and payload_parent != run_id:
            raise EdgeDetailImportError("EDGE_IMPORT_PARENT_ID_MISMATCH")
        records.extend(part_records)
    repo = ProductionRepository(database_path(cfg))
    current = {str(row["sku"]): row for row in repo.load_current_export_records()}
    valid = _validate_records(payload, records, candidates, current, allow_already_enriched=True,
                              allow_incomplete=True, skip_unverified=True)
    master_path = Path(cfg["paths"]["master"])
    if not master_path.exists():
        raise EdgeDetailImportError(f"EDGE_STAGING_MASTER_MISSING:{master_path}")
    _validate_master_es_schema(master_path)
    master_records = load_current(master_path)
    lifecycle_records = repo.load_known_skus()
    rows = []
    missing_fields: dict[str, list[str]] = {}
    detail_merge_ready: list[str] = []
    for row in valid:
        sku = row["sku"]
        base = master_records.get(sku)
        if not base:
            raise EdgeDetailImportError(f"EDGE_STAGING_MASTER_BASE_MISSING:{sku}")
        merged = {key: base.get(key) for key in ES_MAP.values()}
        # Lifecycle is the authoritative owner of lifecycle dates.  Master
        # may have a legacy blank in these columns; hydrate the candidate
        # without changing the database or the source facts.
        lifecycle = lifecycle_records.get(sku) or {}
        merged["first_seen"] = merged.get("first_seen") or lifecycle.get("first_seen_date")
        merged["last_seen"] = merged.get("last_seen") or lifecycle.get("last_seen_date")
        if not merged.get("match_status"):
            merged["match_status"] = "PENDING_REVIEW"
        # Edge is detail-only evidence.  It cannot replace listing-owned
        # name/category/spec/URL values from the Master base row.
        if row.get("cat2_es") and not merged.get("cat2_es"):
            # The second breadcrumb is a listing fact.  Fill only a legacy
            # blank; never overwrite an existing Master category silently.
            merged["cat2_es"] = row["cat2_es"]
        if row.get("desc_es"):
            merged["desc_es"] = _normalize_description(row["desc_es"])
        if row.get("details_es"):
            merged["details_es"] = _normalize_detail_delimiters(row["details_es"])
        # The raw official badge string is the source of truth for the
        # human-readable badge columns in a staging workbook.  Older Master
        # rows can carry ``raw_tags=Nuevo`` while the legacy boolean is still
        # false; deriving these display values here prevents that mismatch
        # without changing PRIMARY/Master data.
        badges = parse_badges(merged.get("raw_tags"))
        if merged.get("raw_tags"):
            merged["is_new_badge"] = badges.action_new_badge
            merged["promotion"] = badges.promotion_active
            merged["sustainable"] = badges.sustainable_badge
            if badges.discount is not None:
                merged["discount"] = badges.discount
        output_row = {header: merged.get(internal_key) for header, internal_key in ES_MAP.items()}
        for key in ("is_new_badge", "promotion", "sustainable"):
            if output_row.get(ES_MAP_INV[key]) is not None:
                output_row[ES_MAP_INV[key]] = "是" if output_row[ES_MAP_INV[key]] else "否"
        for header in _DATE_HEADERS:
            if header in output_row:
                output_row[header] = _excel_date(output_row[header])
        rows.append(output_row)
        missing = [header for header, internal_key in ES_MAP.items() if output_row.get(header) in (None, "")]
        if missing:
            missing_fields[sku] = missing
        if not any(output_row.get(header) in (None, "") for header in MASTER_DETAIL_MERGE_HEADERS):
            detail_merge_ready.append(sku)
    result = write_catalog_xlsx(
        Path(output_path), headers=EDGE_STAGING_HEADERS, rows=rows,
        workbook_format={
            "sheet_name": "02_SKU_ES_CURRENT_待审核",
            "header": {"fill": "0F4C5C", "bold": True, "font_color": "FFFFFF", "wrap_text": True},
            "auto_filter": True, "freeze_panes": "A2",
            "body": {"wrap_text_columns": ["描述（西语）", "产品详情（西语）"]},
            "date_columns": sorted(_DATE_HEADERS),
            "column_widths": {
                "Canonical_ID": 18, "SKU": 14, "西班牙语品名": 34,
                "当前售价 (€)": 14, "原价 (€)": 14, "上次售价 (€)": 14,
                "最近一次变价方向": 18, "本期价格变化": 16, "变化金额 (€)": 14,
                "变化幅度 (%)": 14, "最近变价日期": 16, "历史最低价 (€)": 14,
                "历史最高价 (€)": 14, "一级类目（西语）": 20, "二级类目（西语）": 22,
                "规格（西语）": 30, "单价": 16, "新品": 10, "促销": 10,
                "可持续": 10, "折扣": 10, "原始标签": 24, "当前状态": 14,
                "首次发现日期": 16, "最后确认存在日期": 18, "描述（西语）": 42,
                "产品详情（西语）": 58, "商品链接": 58, "图片链接": 42, "匹配状态": 18,
            },
        },
    )
    ready_count = len(rows) - len(missing_fields)
    return {"status": "STAGED" if ready_count == len(rows) else "STAGED_WITH_GAPS",
            "parent_run_id": run_id, "input_rows": len(records),
            "valid_rows": len(valid), "skipped_unverified": len(records) - len(valid),
            "output": str(output_path), "master_aligned": True,
            "master_schema_headers": EDGE_STAGING_HEADERS,
            "missing_fields_by_sku": missing_fields,
            "rows_ready_for_master": ready_count,
            "rows_ready_for_detail_merge": len(detail_merge_ready),
            "detail_merge_ready_skus": detail_merge_ready,
            **result}


def run_deferred_detail_import(cfg: dict[str, Any], *, run_id: str, input_path: Path,
                               commit: bool = False) -> dict[str, Any]:
    """Import explicitly deferred MISSING_FIELD details from a QA-passing run."""
    parent, _ = _validate_parent(cfg, run_id, for_staging=True)
    report = json.loads((parent / "run_report.json").read_text(encoding="utf-8"))
    if report.get("dry_run") or report.get("commit_status") != "FULL_COMMIT":
        raise EdgeDetailImportError("DEFERRED_IMPORT_PARENT_NOT_COMMITTED")
    import csv
    with (parent / "product_updates.csv").open("r", encoding="utf-8-sig", newline="") as handle:
        updates = list(csv.DictReader(handle))
    deferred = {
        str(row.get("sku") or "").strip() for row in updates
        if str(row.get("reason") or "").strip() == "MISSING_FIELD"
        and str(row.get("detail_selected") or "").strip().lower() in {"", "0", "false", "no"}
    }
    if not deferred:
        raise EdgeDetailImportError("DEFERRED_IMPORT_QUEUE_EMPTY")
    payload, records = _read_payload(Path(input_path))
    if payload.get("parent_run_id") and str(payload["parent_run_id"]).strip() != run_id:
        raise EdgeDetailImportError("EDGE_IMPORT_PARENT_ID_MISMATCH")
    supplied = {str(row.get("sku") or "").strip() for row in records}
    if not supplied <= deferred:
        raise EdgeDetailImportError(f"DEFERRED_IMPORT_SKU_NOT_DEFERRED:{sorted(supplied - deferred)[:5]}")
    repo = ProductionRepository(database_path(cfg))
    current = {str(row["sku"]): row for row in repo.load_current_export_records()}
    valid = _validate_records(payload, records, deferred, current)
    if not valid:
        raise EdgeDetailImportError("DEFERRED_IMPORT_NO_VERIFIED_RECORDS")
    result: dict[str, Any] = {"status": "PREVIEW", "parent_run_id": run_id,
                              "input_rows": len(records), "valid_rows": len(valid),
                              "deferred_count": len(deferred), "commit": bool(commit)}
    if not commit:
        return result
    import_id = f"deferred-detail-import_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{len(valid)}"
    evidence_dir = parent / "detail_edge_imports" / import_id
    evidence_dir.mkdir(parents=True, exist_ok=False)
    (evidence_dir / "input.json").write_text(json.dumps(payload or {"records": records}, ensure_ascii=False, indent=2), encoding="utf-8")
    (evidence_dir / "records.json").write_text(json.dumps(valid, ensure_ascii=False, indent=2), encoding="utf-8")
    apply_result = apply_detail_only_updates(database_path(cfg), valid, import_id=import_id,
                                             evidence={"parent_run_id": run_id, "source": "EDGE_PLUGIN",
                                                       "kind": "DEFERRED_MISSING_FIELD"})
    head = repo.current_head()
    sync = regenerate_compatibility_exports(cfg, head) if head else None
    final = {**result, **apply_result, "status": "APPLIED", "import_id": import_id,
             "master_sync": sync, "finished_at": datetime.now(timezone.utc).isoformat()}
    (evidence_dir / "import_report.json").write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    return final


def _normalize_detail_delimiters(value: Any) -> str | None:
    """Match Master detail convention while preserving source text values."""
    if value in (None, ""):
        return None
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if not lines:
        return None
    pairs: list[str] = []
    for line in lines:
        # Browser evidence may arrive as newline-delimited rows or as one
        # pipe-delimited line.  Normalize both forms without leaving tabs in
        # the workbook.
        for segment in line.split("|"):
            segment = segment.strip()
            if not segment:
                continue
            if "\t" in segment:
                pairs.extend(part.strip() for part in segment.split("\t") if part.strip())
            else:
                pairs.append(segment)
    return "; ".join(part for part in pairs if part)


def run_edge_detail_import(cfg: dict[str, Any], *, run_id: str, input_path: Path, commit: bool = False) -> dict[str, Any]:
    """Validate an Edge JSON export and optionally apply it to PRIMARY."""
    parent, candidates = _validate_parent(cfg, run_id)
    payload, records = _read_payload(Path(input_path))
    payload_parent = str(payload.get("parent_run_id") or "").strip()
    if payload_parent and payload_parent != run_id:
        raise EdgeDetailImportError("EDGE_IMPORT_PARENT_ID_MISMATCH")
    repo = ProductionRepository(database_path(cfg))
    current = {str(row["sku"]): row for row in repo.load_current_export_records()}
    valid = _validate_records(payload, records, candidates, current)
    result: dict[str, Any] = {"status": "PREVIEW", "parent_run_id": run_id,
                              "input_rows": len(records), "valid_rows": len(valid),
                              "candidate_count": len(candidates), "commit": bool(commit)}
    if not commit:
        return result
    import_id = f"edge-import_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{len(valid)}"
    evidence_dir = parent / "detail_edge_imports" / import_id
    evidence_dir.mkdir(parents=True, exist_ok=False)
    (evidence_dir / "input.json").write_text(json.dumps(payload or {"records": records}, ensure_ascii=False, indent=2), encoding="utf-8")
    (evidence_dir / "records.json").write_text(json.dumps(valid, ensure_ascii=False, indent=2), encoding="utf-8")
    apply_result = apply_detail_only_updates(database_path(cfg), valid, import_id=import_id,
                                             evidence={"parent_run_id": run_id, "source": "EDGE_PLUGIN"})
    head = repo.current_head()
    if head:
        sync = regenerate_compatibility_exports(cfg, head)
    else:
        sync = None
    report = {**result, **apply_result, "status": "APPLIED", "import_id": import_id,
              "master_sync": sync, "finished_at": datetime.now(timezone.utc).isoformat()}
    (evidence_dir / "import_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
