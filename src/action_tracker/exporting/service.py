"""正式商品清单导出服务：只读来源、严格验证、生成 Excel 与 manifest。"""
from __future__ import annotations

import csv
import datetime as dt
import html
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import openpyxl
from PIL import Image, UnidentifiedImageError

from ..excel.reader import load_current
from ..services.normalization import parse_bool_zh, parse_price
from .dictionary_join import (
    DictionaryJoinError,
    build_zh_rows,
    build_zh_rows_from_localized_source,
    load_dictionary_context,
    unresolved_brand_ids_for_records,
)
from .repair import ExportRepairEngine, default_overrides_path
from .repair_report import ExportRepairReport, audit_repaired_rows
from .excel_writer import write_catalog_xlsx
from .profiles import ExportProfile, ExportProfileError, load_profile


class ExportValidationError(ValueError):
    """正式导出来源或记录不满足冻结契约。"""


PREVIEW = "PREVIEW"
PRODUCTION_RELEASE = "PRODUCTION_RELEASE"
_HASHED_ZH_COLUMNS = ("编号", "标题", "分类1", "分类2", "规格", "单价", "描述", "产品详情", "折后价", "原价", "图片链接", "商品链接")


def export_output_path(cfg: dict[str, Any], filename: str, mode: str) -> Path:
    """Preview has a dedicated namespace; existing unknown bundles are protected."""
    mode = _release_mode(mode)
    root = Path(cfg["paths"]["exports"]).resolve()
    directory = root / "preview" if mode == PREVIEW else root
    if directory.resolve() != directory:
        raise ExportValidationError("EXPORT_DIRECTORY_ALIAS_FORBIDDEN")
    output = directory / filename
    if output.name != filename or output.resolve().parent != directory:
        raise ExportValidationError("EXPORT_PATH_OUTSIDE_PUBLICATION_DIRECTORY")
    if mode == PREVIEW:
        validate_preview_destination(output)
    return output


def validate_preview_destination(output: Path) -> None:
    paths = (output, output.with_suffix(".manifest.json"), output.with_suffix(".repair-report.json"))
    if not any(path.exists() or path.is_symlink() for path in paths):
        return
    try:
        manifest = json.loads(paths[1].read_text(encoding="utf-8"))
        trusted = (
            not any(path.is_symlink() for path in paths)
            and manifest.get("release_mode") == "preview"
            and manifest.get("output_file") == output.name
            and output.is_file()
            and manifest.get("output_sha256") == hashlib.sha256(output.read_bytes()).hexdigest()
        )
        if paths[2].exists():
            repair = json.loads(paths[2].read_text(encoding="utf-8"))
            trusted = trusted and manifest.get("repair_report_digest") == _json_digest(repair)
        elif manifest.get("repair_report_digest"):
            trusted = False
    except (OSError, ValueError, TypeError, AttributeError):
        trusted = False
    if not trusted:
        raise ExportValidationError("PREVIEW_EXISTING_ARTIFACT_UNTRUSTED")


def _json_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def validate_production_release(cfg: dict[str, Any], source: "ExportSource", rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The same complete strict gate for catalog and Template 1 publication."""
    if source.kind != "SQLITE_CURRENT":
        raise ExportValidationError("PRODUCTION_RELEASE_REQUIRES_SQLITE_CURRENT")
    validate_spanish_source_fields(source.records)
    validate_zh_rows_against_source(rows, source.records)
    if [str(row.get('编号') or '') for row in rows] != [str(row.get('编号') or '') for row in build_es_rows(source.records)]:
        raise ExportValidationError('RESEARCH_RELEASE_ES_ZH_ORDER_MISMATCH')
    from ..localization.release_gate import audit_research_release, load_allowed_tokens, load_explicit_exceptions
    from ..data_quality.master_gate import audit_master_quality
    try:
        master_quality = audit_master_quality(_database_path(cfg)).as_dict()
    except Exception as exc:
        raise ExportValidationError(f"MASTER_QUALITY_GATE_ERROR:{type(exc).__name__}") from exc
    release = audit_research_release(
        source.records, expected_skus={str(r.get("sku") or "") for r in source.records},
        exported_rows=rows,
        allowed_tokens=load_allowed_tokens(Path(cfg["project_root"]) / "data" / "dictionary"),
        explicit_exceptions=load_explicit_exceptions(Path(cfg["project_root"]) / "config" / "research_release_exceptions.json"),
        master_quality=master_quality,
    )
    if not release.ok:
        raise ExportValidationError(f"RESEARCH_RELEASE_GATE_FAILED:{','.join(release.issues[:8])}")
    return {"status": "PASS", "es_audit": "PASS", "zh_audit": "PASS", "parity": "PASS", "master_quality": master_quality}


def _export_repair_allowed_tokens(dictionary: Any) -> set[str]:
    """Return the reviewed display-token allowlist used by repair and audit."""
    from ..dictionary import is_confirmed_brand_record
    from ..localization.release_gate import load_allowed_tokens

    tokens = set(load_allowed_tokens(Path(dictionary.directory)))
    for row in dictionary.brand_by_id.values():
        if not is_confirmed_brand_record(row):
            continue
        for value in (row.get("canonical_name"), row.get("brand_id")):
            if value:
                tokens.add(str(value).strip())
        aliases = str(row.get("aliases_es") or "")
        tokens.update(piece.strip() for piece in re.split(r"[,;|]", aliases) if piece.strip())
    return tokens


def _database_path(cfg: dict[str, Any]) -> Path:
    from ..database.integration import database_path
    return database_path(cfg)


@dataclass(frozen=True)
class ExportSource:
    export_date: str
    run_id: str
    kind: str
    records: tuple[dict[str, Any], ...]
    source_master_file_hash: str | None
    directory: Path | None = None
    source_commit_id: str | None = None


@dataclass(frozen=True)
class FinalZhProjection:
    """The sole auditable/publishable Chinese row identity for an export."""
    rows: tuple[dict[str, Any], ...]
    fallback_counts: dict[str, int]
    dictionary_hash: str | None
    unresolved_brand_ids: tuple[str, ...]
    allowed_tokens: frozenset[str]
    repair_report: ExportRepairReport
    repair_audit: dict[str, Any]
    rows_hash: str


def build_final_zh_projection(
    cfg: dict[str, Any], source: ExportSource, *, release_mode: str = PREVIEW,
) -> FinalZhProjection:
    """Build, finalize and audit final Chinese rows exactly once.

    Every consumer, including Workflow V2 and Template1, must consume this
    projection rather than reconstructing a second Chinese row set.
    """
    mode = _release_mode(release_mode)
    report = ExportRepairReport(run_id=source.run_id, rule_version=ExportRepairEngine.VERSION)
    dictionary_hash: str | None = None
    unresolved_brand_ids: list[str] = []
    dictionary = None
    allowed_tokens: set[str] = set()
    try:
        if (cfg.get("paths") or {}).get("dictionary_baseline"):
            dictionary = load_dictionary_context(cfg)
            dictionary_hash = dictionary.content_hash
            allowed_tokens = _export_repair_allowed_tokens(dictionary)
        if source.kind == "SQLITE_CURRENT":
            rows, fallbacks = build_zh_rows_from_localized_source(
                source.records, category_context=dictionary, repair_report=report,
                repair_overrides_path=default_overrides_path(Path(cfg["project_root"])),
            )
        else:
            if dictionary is None:
                raise ExportValidationError("FORMAL_DICTIONARY_MISSING")
            records = source.records
            if source.kind == "FORMAL_SNAPSHOT":
                records, _ = _reuse_source_bound_localizations(cfg, source.records)
            rows, fallbacks = build_zh_rows(
                records, dictionary, repair_report=report,
                repair_overrides_path=default_overrides_path(Path(cfg["project_root"])),
            )
            unresolved_brand_ids = unresolved_brand_ids_for_records(source.records, dictionary)
    except DictionaryJoinError as exc:
        raise ExportValidationError(str(exc)) from exc
    validate_zh_rows_against_source(rows, source.records)
    validate_output_rows(rows)
    # Finalize can discover empty targets. It must run before the audit and
    # release-ready calculation, otherwise a later blocker can be missed.
    report.finalize(
        source.records, rows, source_hash=canonical_source_hash(source.records),
        sku_set_hash=_sku_set_hash(str(row["编号"]) for row in rows),
    )
    audit = audit_repaired_rows(source.records, rows, allowed_tokens=allowed_tokens)
    report.set_audit(audit)
    audit = dict(report.audit)
    rows_hash = canonical_export_rows_hash(rows)
    if mode == PRODUCTION_RELEASE:
        if source.kind != "SQLITE_CURRENT":
            raise ExportValidationError("PRODUCTION_RELEASE_REQUIRES_SQLITE_CURRENT")
        if not bool(audit.get("release_ready")):
            raise ExportValidationError(
                "EXPORT_REPAIR_GATE_FAILED:"
                f"p0={audit.get('p0_findings', 0)};"
                f"affected_fields={audit.get('blocking_affected_fields', 0)};"
                f"unresolved={audit.get('unresolved_blocking_count', 0)}"
            )
    return FinalZhProjection(
        rows=tuple(rows), fallback_counts=dict(fallbacks), dictionary_hash=dictionary_hash,
        unresolved_brand_ids=tuple(unresolved_brand_ids), allowed_tokens=frozenset(allowed_tokens),
        repair_report=report, repair_audit=audit, rows_hash=rows_hash,
    )


_SOURCE_HASH_FIELDS = (
    "sku", "canonical_id", "name_es", "cat1_es", "cat2_es", "spec_es", "current_price",
    "original_price", "unit_price", "desc_es", "details_es", "product_url", "image_url",
    "is_new_badge", "promotion", "sustainable", "discount", "raw_tags", "status",
    "first_seen", "last_seen",
)
_CJK_RE = re.compile(r"[\u3400-\u9fff]")


def export_catalog(
    cfg: dict[str, Any],
    *,
    language: str,
    export_date: str,
    no_images: bool,
    run_id: str | None = None,
    selection_id: str | None = None,
    research_release: bool = False,
    release_mode: str = PREVIEW,
) -> dict[str, Any]:
    """导出一个正式全量清单；整个过程只读取来源并写入 exports 目录。"""
    _validate_date(export_date)
    try:
        profile = load_profile(cfg, language=language, no_images=no_images)
    except ExportProfileError as exc:
        raise ExportValidationError(str(exc)) from exc
    if profile.language != language:
        raise ExportValidationError(f"EXPORT_PROFILE_LANGUAGE_MISMATCH: {profile.profile_id}")
    source = resolve_formal_source(cfg, export_date=export_date, requested_run_id=run_id, profile=profile)
    if research_release:
        release_mode = PRODUCTION_RELEASE
    release_mode = _release_mode(release_mode)
    if release_mode == PRODUCTION_RELEASE:
        if language != "zh":
            raise ExportValidationError("RESEARCH_RELEASE_ZH_ONLY")
        if source.kind != "SQLITE_CURRENT":
            raise ExportValidationError("RESEARCH_RELEASE_REQUIRES_SQLITE_APPLIED_SOURCE")
    artifact_source_commit_id = source.source_commit_id or _commit_id_for_run(_database_path(cfg), source.run_id)
    selection_source_commit_id = None
    if selection_id:
        from ..extraction.selections import SelectionService
        selection = SelectionService(_database_path(cfg)).get(selection_id)
        if not selection:
            raise ExportValidationError(f"SELECTION_NOT_FOUND: {selection_id}")
        members = set(selection["members"]); source_skus = {str(r.get("sku") or "") for r in source.records}
        missing = sorted(members - source_skus)
        if missing:
            raise ExportValidationError(f"SELECTION_MEMBER_NOT_IN_FORMAL_SOURCE: {','.join(missing[:10])}")
        source = ExportSource(source.export_date, source.run_id, source.kind,
                              tuple(r for r in source.records if str(r.get("sku") or "") in members),
                              source.source_master_file_hash, source.directory, source.source_commit_id)
        selection_source_commit_id = selection.get("source_commit_id")
    validate_source_records(source.records, export_date=export_date)
    dictionary_hash = None
    fallback_counts: dict[str, int] = {}
    unresolved_brand_ids: list[str] = []
    historical_localization_reuse: dict[str, Any] = {}
    repair_report: ExportRepairReport | None = None
    repair_allowed_tokens: set[str] = set()
    if language == "es":
        validate_spanish_source_fields(source.records)
        rows = build_es_rows(source.records)
    elif language == "zh":
        projection = build_final_zh_projection(cfg, source, release_mode=release_mode)
        rows = [dict(row) for row in projection.rows]
        fallback_counts = projection.fallback_counts
        dictionary_hash = projection.dictionary_hash
        unresolved_brand_ids = list(projection.unresolved_brand_ids)
        repair_allowed_tokens = set(projection.allowed_tokens)
        repair_report = projection.repair_report
        repair_audit = projection.repair_audit
        final_zh_rows_hash = projection.rows_hash
    else:
        raise ExportValidationError(f"EXPORT_LANGUAGE_UNSUPPORTED: {language}")
    validate_output_rows(rows)
    repair_audit: dict[str, Any] | None = locals().get("repair_audit")
    repair_report_path: Path | None = None
    final_zh_rows_hash = locals().get("final_zh_rows_hash")
    if release_mode == PRODUCTION_RELEASE:
        release_audit = validate_production_release(cfg, source, rows)

    date_compact = export_date.replace("-", "")
    output_name = profile.filename_for(date_compact)
    if selection_id:
        output_name = output_name.replace(".xlsx", f"_Selection_{selection_id}.xlsx")
    output_path = export_output_path(cfg, output_name, release_mode)
    headers = [str(column["header"]) for column in profile.columns]
    expected_skus = {str(r["编号"]) for r in rows}
    image_root = (Path(cfg["paths"]["images"]) / "derivatives" / "excel_250") if not no_images else None
    image_eligibility = _resolve_image_eligibility(cfg, source.records, image_root) if image_root else None
    # 先写入并验证旁路临时文件；工作簿和 manifest 通过校验后成对发布，
    # 避免验证失败或 manifest 写入失败时留下半套导出物。
    preview_path = output_path.with_name(f".{output_path.stem}.{uuid.uuid4().hex}.preview.xlsx")
    try:
        image_stats = write_catalog_xlsx(
            preview_path, headers=headers, rows=rows, workbook_format=profile.workbook_format,
            image_root=image_root,
            embed_images=not no_images,
            image_eligibility=image_eligibility,
        )
        _verify_written_workbook(preview_path, headers=headers, expected_skus=expected_skus,
                                 expect_images=not no_images, expected_image_count=image_stats["embedded_count"])
        if not no_images and image_stats["embedded_count"] + image_stats["missing_count"] != len(rows):
            raise ExportValidationError("EXPORT_IMAGE_COVERAGE_MISMATCH")
        source_hash = canonical_source_hash(source.records)
        manifest = {
            "run_id": source.run_id,
            "export_date": export_date,
            "sku_count": len(rows),
            "sku_set_hash": _sku_set_hash(expected_skus),
            "source_master_hash": source_hash,
            "source_master_file_hash": source.source_master_file_hash,
            "source_kind": source.kind,
            "profile_id": profile.profile_id,
            "profile_version": profile.version,
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "output_file": output_path.name,
            "validation_results": {
                "source_records": "PASS",
                "output_rows": "PASS",
                "workbook": "PASS",
            },
            "detail_retry_ids": _detail_retry_ids(source),
            "image_profile": "excel_250_white_v1" if not no_images else None,
            "image_embedded_count": image_stats["embedded_count"],
            "image_missing_count": image_stats["missing_count"],
            "missing_image_skus": sorted(image_stats.get("missing_skus", [])),
            "image_eligible_count": sum(1 for eligible in (image_eligibility or {}).values() if eligible),
            "selection_id": selection_id,
            "selection_source_commit_id": selection_source_commit_id,
            "artifact_source_commit_id": artifact_source_commit_id,
            "release_mode": release_mode.casefold(),
            "output_sha256": hashlib.sha256(preview_path.read_bytes()).hexdigest(),
        }
        if release_mode == PRODUCTION_RELEASE:
            manifest["strict_release_audit"] = release_audit
        if language == "zh":
            manifest["dictionary_hash"] = dictionary_hash
            manifest["dictionary_fallback_counts"] = fallback_counts
            manifest["dictionary_unresolved_brand_ids"] = unresolved_brand_ids
            if historical_localization_reuse:
                manifest["historical_localization_reuse"] = historical_localization_reuse
            manifest["export_repair"] = {
                "engine_version": ExportRepairEngine.VERSION,
                "audit_findings": int((repair_audit or {}).get("audit_findings") or 0),
                "affected_fields": int((repair_audit or {}).get("affected_fields") or 0),
                "affected_skus": int((repair_audit or {}).get("affected_skus") or 0),
                "field_pass_rate": float((repair_audit or {}).get("field_pass_rate") or 0),
                "p0_findings": int((repair_audit or {}).get("p0_findings") or 0),
                "unresolved_blocking_count": int((repair_audit or {}).get("unresolved_blocking_count") or 0),
                "release_ready": bool((repair_audit or {}).get("release_ready")),
                "repair_count": len(repair_report.events) if repair_report else 0,
                "audited_zh_rows_hash": final_zh_rows_hash,
            }
        manifest_path = output_path.with_suffix(".manifest.json")
        if repair_report is not None:
            repair_report_path = output_path.with_suffix(".repair-report.json")
            manifest["export_repair"]["repair_report"] = repair_report_path.name
        published_zh_rows_hash = None
        if language == "zh":
            published_zh_rows_hash = canonical_export_rows_hash(_read_catalog_rows(preview_path, headers))
            if published_zh_rows_hash != final_zh_rows_hash:
                raise ExportValidationError("PUBLISHED_ROWS_DIFFER_FROM_AUDITED_ROWS")
            manifest["export_repair"]["published_zh_rows_hash"] = published_zh_rows_hash
            manifest["export_repair"]["release_ready"] = bool((repair_audit or {}).get("release_ready"))
        repair_payload = repair_report.as_dict() if repair_report is not None else None
        if repair_payload is not None:
            manifest["repair_report_digest"] = _json_digest(repair_payload)
        if release_mode == PREVIEW:
            validate_preview_destination(output_path)
        _publish_export_bundle(
            preview_path, output_path, manifest_path, manifest,
            repair_report_path=repair_report_path,
            repair_report=repair_payload,
        )
        if selection_id:
            from ..delivery.artifacts import ArtifactService
            ArtifactService(_database_path(cfg)).record(
                artifact_id=f"artifact_{hashlib.sha256((str(output_path)+source.run_id).encode()).hexdigest()[:16]}",
                artifact_type="XLSX", file_path=output_path, row_count=len(rows), selection_id=selection_id,
                profile_id=profile.profile_id, language=language,
                image_profile="excel_250_white_v1" if not no_images else None,
                source_commit_id=artifact_source_commit_id, selection_source_commit_id=selection_source_commit_id,
                manifest_path=manifest_path,
            )
    finally:
        if preview_path.exists():
            preview_path.unlink()
    return {
        "output": str(output_path),
        "manifest": str(manifest_path),
        "run_id": source.run_id,
        "sku_count": len(rows),
        "source_kind": source.kind,
        "profile": profile.profile_id,
        "image_embedded_count": image_stats["embedded_count"],
        "image_missing_count": image_stats["missing_count"],
        "missing_image_skus": sorted(image_stats.get("missing_skus", [])),
        "image_eligible_count": sum(1 for eligible in (image_eligibility or {}).values() if eligible),
        "selection_id": selection_id,
        "artifact_source_commit_id": artifact_source_commit_id,
        "release_mode": release_mode.casefold(),
        "repair_report": str(repair_report_path) if repair_report_path else None,
        "repair_audit": repair_audit,
        "audited_zh_rows_hash": final_zh_rows_hash,
        "published_zh_rows_hash": published_zh_rows_hash,
    }


_HISTORICAL_LOCALIZATION_FIELDS = (
    ("name_es", "name_zh"),
    ("spec_es", "spec_zh"),
    ("desc_es", "desc_zh"),
    ("details_es", "details_zh"),
)


def _reuse_source_bound_localizations(
    cfg: dict[str, Any], records: Iterable[dict[str, Any]],
) -> tuple[tuple[dict[str, Any], ...], dict[str, Any]]:
    """Reuse PRIMARY Chinese values for an immutable historical snapshot.

    The snapshot remains authoritative for SKU membership and every Spanish
    fact. A Chinese field is copied only when the same SKU exists in PRIMARY,
    that field's Spanish source is unchanged, the localization is fresh, and
    the target visibly contains Chinese. This keeps the reuse field-scoped and
    prevents a newer product revision from leaking into an older export.
    """
    if str((cfg.get("storage") or {}).get("mode") or "EXCEL_PRIMARY").upper() != "SQLITE_PRIMARY":
        return tuple(records), {}

    from ..database.repository import ProductionRepository, ProductionRepositoryError

    try:
        references = ProductionRepository(_database_path(cfg)).load_current_export_records(
            include_non_current=True,
        )
    except ProductionRepositoryError as exc:
        raise ExportValidationError(f"HISTORICAL_LOCALIZATION_REFERENCE_ERROR: {exc}") from exc

    reference_by_sku = {
        str(record.get("sku") or "").strip(): record
        for record in references
        if str(record.get("sku") or "").strip()
    }
    field_counts = {target: 0 for _, target in _HISTORICAL_LOCALIZATION_FIELDS}
    source_changed_counts = {target: 0 for _, target in _HISTORICAL_LOCALIZATION_FIELDS}
    no_reference_count = 0
    output: list[dict[str, Any]] = []
    for record in records:
        hydrated = dict(record)
        sku = str(record.get("sku") or "").strip()
        reference = reference_by_sku.get(sku)
        if reference is None:
            no_reference_count += 1
            output.append(hydrated)
            continue
        source_bound_fields: list[str] = []
        from ..localization.provenance import coerce_field_provenance
        provenance = coerce_field_provenance(reference.get("zh_field_provenance"))
        for source_field, target_field in _HISTORICAL_LOCALIZATION_FIELDS:
            historical_source = _normalized_source_identity(record.get(source_field))
            current_source = _normalized_source_identity(reference.get(source_field))
            if not historical_source:
                # Source-absent fields must remain absent in the target.
                hydrated[target_field] = None
                continue
            if historical_source != current_source:
                source_changed_counts[target_field] += 1
                continue
            canonical = {
                "name_zh": "name", "spec_zh": "spec",
                "desc_zh": "description", "details_zh": "details",
            }[target_field]
            metadata = provenance.get(canonical) or {}
            freshness = str(
                metadata.get("freshness_status")
                or reference.get("zh_freshness_status")
                or ""
            ).upper()
            if freshness not in {"CURRENT", "FRESH"}:
                continue
            target_value = str(metadata.get("value") or reference.get(target_field) or "").strip()
            if not target_value or not _CJK_RE.search(target_value):
                continue
            hydrated[target_field] = target_value
            source_bound_fields.append(target_field)
            field_counts[target_field] += 1
        if source_bound_fields:
            hydrated["_source_bound_localization_fields"] = tuple(source_bound_fields)
        output.append(hydrated)

    return tuple(output), {
        "reference": "SQLITE_PRIMARY_FIELD_SOURCE_MATCH",
        "field_reuse_counts": field_counts,
        "source_changed_counts": source_changed_counts,
        "sku_without_reference_count": no_reference_count,
    }


def _normalized_source_identity(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.strip().split("\n"))
    # The detail parser historically inserted a separator inside a compound
    # Spanish key (for example ``Material: de revestimiento``). Treat only
    # those connector forms as punctuation-equivalent; values after a real
    # key separator remain significant.
    text = re.sub(
        r"\b(Capacidad|Material|Tama(?:ñ|n)o):\s+(?=(?:de\b|del\b|máx\.|max\.))",
        r"\1 ", text, flags=re.IGNORECASE,
    )
    # Quantity abbreviations and their expanded form own the same fact.
    text = re.sub(
        r"\b(?:uds?|unid(?:ades)?)\.?(?=\s|$)",
        "unidades", text, flags=re.IGNORECASE,
    )
    return text


def _commit_id_for_run(db_path: Path, run_id: str | None) -> str | None:
    if not run_id or not db_path.exists():
        return None
    from ..database.connection import connect
    with connect(db_path) as db:
        row = db.execute("SELECT commit_id FROM commit_batches WHERE run_id=? AND status='COMMITTED'", (run_id,)).fetchone()
    return str(row[0]) if row else None


def resolve_formal_source(
    cfg: dict[str, Any],
    *,
    export_date: str,
    requested_run_id: str | None,
    profile: ExportProfile,
) -> ExportSource:
    """解析唯一正式 run；最新正式 run 才允许直接读取 Master CURRENT。"""
    # Once SQLite PRIMARY is active, the latest committed DB head is the
    # authoritative CURRENT source for that date. Historical dates continue to
    # use immutable formal snapshots because the DB is append-only and does not
    # expose a time-travel CURRENT view yet.
    if str((cfg.get("storage") or {}).get("mode") or "EXCEL_PRIMARY").upper() == "SQLITE_PRIMARY":
        sqlite_source = _resolve_sqlite_current_source(
            cfg, export_date=export_date, requested_run_id=requested_run_id,
        )
        if sqlite_source is not None:
            return sqlite_source
    candidates = _formal_snapshot_candidates(Path(cfg["paths"]["snapshots"]), export_date, profile.source_policy)
    if requested_run_id:
        candidates = [candidate for candidate in candidates if candidate["run_id"] == requested_run_id]
        if not candidates:
            raise ExportValidationError(f"FORMAL_RUN_NOT_FOUND: {requested_run_id} on {export_date}")
    if not candidates:
        raise ExportValidationError(f"NO_FORMAL_EXPORT_SOURCE: {export_date}")
    if len(candidates) != 1:
        raise ExportValidationError(f"AMBIGUOUS_FORMAL_EXPORT_SOURCE: {export_date}")
    selected = candidates[0]
    master = Path(cfg["paths"]["master"])
    latest_master_run = _latest_successful_master_run(master, profile.source_policy)
    if latest_master_run == selected["run_id"]:
        records = tuple(load_current(master).values())
        return ExportSource(
            export_date=export_date,
            run_id=selected["run_id"],
            kind="MASTER_CURRENT",
            records=records,
            source_master_file_hash=_sha256_file(master),
            directory=selected["directory"],
        )
    snapshot_records = _read_csv_records(selected["directory"] / "products_normalized.csv")
    records = tuple(_merge_detail_retries(selected["directory"], snapshot_records))
    if not records:
        raise ExportValidationError(f"FORMAL_SNAPSHOT_PRODUCTS_MISSING: {selected['directory']}")
    return ExportSource(
        export_date=export_date,
        run_id=selected["run_id"],
        kind="FORMAL_SNAPSHOT",
        records=records,
        source_master_file_hash=None,
        directory=selected["directory"],
    )


def _resolve_sqlite_current_source(
    cfg: dict[str, Any], *, export_date: str, requested_run_id: str | None,
) -> ExportSource | None:
    from ..database.integration import database_path
    from ..database.repository import ProductionRepository, ProductionRepositoryError

    try:
        repo = ProductionRepository(database_path(cfg))
        info = repo.commit_info(requested_run_id) if requested_run_id else repo.latest_commit_info()
    except ProductionRepositoryError as exc:
        raise ExportValidationError(str(exc)) from exc
    if not info or str(info.get("run_date") or "") != export_date:
        return None
    latest = repo.latest_commit_info()
    if latest and info.get("run_id") != latest.get("run_id"):
        # A historical commit cannot be reconstructed from the current DB
        # projection; force the immutable snapshot path instead.
        return None
    records = tuple(repo.load_current_export_records())
    if not records:
        raise ExportValidationError("SQLITE_CURRENT_SOURCE_EMPTY")
    return ExportSource(
        export_date=export_date, run_id=str(info["run_id"]), kind="SQLITE_CURRENT",
        records=records, source_master_file_hash=None, directory=None, source_commit_id=str(info["commit_id"]),
    )


def _formal_snapshot_candidates(snapshots: Path, export_date: str, policy: dict[str, Any]) -> list[dict[str, Any]]:
    date_dir = snapshots / export_date
    if not date_dir.exists():
        return []
    allowed_qa = {str(value) for value in policy.get("allowed_qa_states") or ()}
    required_commit = str(policy.get("required_commit_status") or "FULL_COMMIT")
    candidates: list[dict[str, Any]] = []
    for directory in date_dir.iterdir():
        if not directory.is_dir():
            continue
        report_path, qa_path = directory / "run_report.json", directory / "qa_report.json"
        if not report_path.exists() or not qa_path.exists():
            continue
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            qa = json.loads(qa_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ExportValidationError(f"FORMAL_SNAPSHOT_JSON_INVALID: {directory}") from exc
        if not isinstance(report, dict) or not isinstance(qa, dict):
            raise ExportValidationError(f"FORMAL_SNAPSHOT_JSON_INVALID: {directory}")
        run_id = str(report.get("run_id") or "")
        if not run_id or str(report.get("run_date") or report.get("observation_date") or "") != export_date:
            continue
        if str(report.get("commit_status") or "") != required_commit:
            continue
        if policy.get("reject_dry_run", True) and _is_truthy(report.get("dry_run")):
            continue
        if not bool(qa.get("passed")) or str(qa.get("state") or "") not in allowed_qa:
            continue
        candidates.append({"run_id": run_id, "directory": directory, "report": report, "qa": qa})
    return candidates


def _latest_successful_master_run(master: Path, policy: dict[str, Any]) -> str | None:
    if not master.exists():
        raise ExportValidationError(f"MASTER_MISSING: {master}")
    allowed_qa = {str(value) for value in policy.get("allowed_qa_states") or ()}
    workbook = openpyxl.load_workbook(master, read_only=True, data_only=True)
    try:
        if "05_RUN_LOG" not in workbook.sheetnames:
            raise ExportValidationError("MASTER_RUN_LOG_MISSING")
        ws = workbook["05_RUN_LOG"]
        rows = ws.iter_rows(values_only=True)
        headers = list(next(rows, ()) or ())
        index = {str(value): pos for pos, value in enumerate(headers)}
        required = {"Run ID", "QA状态", "运行状态"}
        if not required.issubset(index):
            raise ExportValidationError(f"MASTER_RUN_LOG_SCHEMA_MISSING: {sorted(required - set(index))}")
        successful: list[str] = []
        for row in rows:
            run_id = str(row[index["Run ID"]] or "").strip()
            qa_state = str(row[index["QA状态"]] or "").strip()
            status = str(row[index["运行状态"]] or "").strip()
            if run_id and qa_state in allowed_qa and status == "SUCCESS":
                successful.append(run_id)
        return successful[-1] if successful else None
    finally:
        workbook.close()


def validate_source_records(records: Iterable[dict[str, Any]], *, export_date: str) -> None:
    """验证 SKU、状态、价格和 URL；不在此处修补来源数据。"""
    rows = list(records)
    if not rows:
        raise ExportValidationError("EXPORT_SOURCE_EMPTY")
    skus: set[str] = set()
    for row in rows:
        sku = str(row.get("sku") or "").strip()
        if not sku:
            raise ExportValidationError("EXPORT_SOURCE_EMPTY_SKU")
        if sku in skus:
            raise ExportValidationError(f"EXPORT_SOURCE_DUPLICATE_SKU: {sku}")
        skus.add(sku)
        if str(row.get("status") or "").strip() != "CURRENT":
            raise ExportValidationError(f"EXPORT_SOURCE_NON_CURRENT_SKU: {sku}")
        current = _required_price(row.get("current_price"), sku=sku, field="current_price")
        original = _optional_price(row.get("original_price"), sku=sku, field="original_price")
        if original is not None and not math.isfinite(original):
            raise ExportValidationError(f"EXPORT_SOURCE_BAD_PRICE: {sku}/original_price")
        product_url = str(row.get("product_url") or "").strip()
        if not _is_http_url(product_url):
            raise ExportValidationError(f"EXPORT_SOURCE_BAD_PRODUCT_URL: {sku}")
        image_url = str(row.get("image_url") or "").strip()
        if image_url and not _is_http_url(image_url):
            raise ExportValidationError(f"EXPORT_SOURCE_BAD_IMAGE_URL: {sku}")
        last_seen = str(row.get("last_seen") or "").strip()
        if not last_seen:
            raise ExportValidationError(f"EXPORT_SOURCE_COLLECTION_DATE_MISSING: {sku}")
        if last_seen[:10] != export_date:
            raise ExportValidationError(f"EXPORT_SOURCE_COLLECTION_DATE_MISMATCH: {sku}/{last_seen}")
        if current <= 0:
            raise ExportValidationError(f"EXPORT_SOURCE_BAD_PRICE: {sku}/current_price")
        if original is not None and original <= 0:
            raise ExportValidationError(f"EXPORT_SOURCE_BAD_PRICE: {sku}/original_price")


def validate_spanish_source_fields(records: Iterable[dict[str, Any]]) -> None:
    """西语事实列含中文说明发生源污染，必须阻断正式 ES 导出。"""
    for row in records:
        sku = str(row.get("sku") or "").strip()
        for field in ("name_es", "cat1_es", "cat2_es", "spec_es", "unit_price", "desc_es", "details_es", "raw_tags"):
            value = _text(row.get(field))
            if value and _CJK_RE.search(value):
                raise ExportValidationError(f"EXPORT_SOURCE_NON_SPANISH_FACT: {sku}/{field}")


def build_es_rows(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """构建 Profile v1 西班牙语行；不使用任何中文字典。"""
    ordered = sorted(records, key=_sku_sort_key)
    return [
        {
            "图片": None,
            "编号": str(record.get("sku") or "").strip(),
            "标题": _text(record.get("name_es")),
            "分类1": _text(record.get("cat1_es")),
            "分类2": _text(record.get("cat2_es")),
            "规格": _text(record.get("spec_es")),
            "折后价": _required_price(record.get("current_price"), sku=str(record.get("sku")), field="current_price"),
            "原价": _display_original_price(record),
            "单价": _text(record.get("unit_price")),
            "描述": _text_or_none(record.get("desc_es")),
            "产品详情": _text_or_none(record.get("details_es")),
            "图片链接": _text_or_none(record.get("image_url")),
            "商品链接": _text(record.get("product_url")),
            "备注": _es_remarks(record),
        }
        for record in ordered
    ]


def validate_output_rows(rows: Iterable[dict[str, Any]]) -> None:
    expected = {"图片", "编号", "标题", "分类1", "分类2", "规格", "折后价", "原价", "单价",
                "描述", "产品详情", "图片链接", "商品链接", "备注"}
    seen: set[str] = set()
    for row in rows:
        if set(row) != expected:
            raise ExportValidationError("EXPORT_OUTPUT_SCHEMA_MISMATCH")
        sku = str(row["编号"] or "").strip()
        if not sku or sku in seen:
            raise ExportValidationError(f"EXPORT_OUTPUT_DUPLICATE_OR_EMPTY_SKU: {sku}")
        seen.add(sku)
        if not isinstance(row["折后价"], (float, int)):
            raise ExportValidationError(f"EXPORT_OUTPUT_PRICE_NOT_NUMERIC: {sku}")
        original = row["原价"]
        if original is not None and (not isinstance(original, (float, int)) or original <= row["折后价"]):
            raise ExportValidationError(f"EXPORT_OUTPUT_INVALID_ORIGINAL_PRICE: {sku}")
        if not _is_http_url(str(row["商品链接"] or "")):
            raise ExportValidationError(f"EXPORT_OUTPUT_BAD_PRODUCT_URL: {sku}")


def validate_zh_rows_against_source(rows: Iterable[dict[str, Any]], records: Iterable[dict[str, Any]]) -> None:
    """中文导出只能改变派生字段，SKU、价格与 URL 必须和正式西语事实完全一致。"""
    expected = {str(record.get("sku") or "").strip(): record for record in records}
    actual = {str(row.get("编号") or "").strip(): row for row in rows}
    if set(actual) != set(expected):
        raise ExportValidationError("ZH_EXPORT_SKU_SET_MISMATCH")
    for sku, record in expected.items():
        row = actual[sku]
        if row["折后价"] != _required_price(record.get("current_price"), sku=sku, field="current_price"):
            raise ExportValidationError(f"ZH_EXPORT_PRICE_MISMATCH: {sku}")
        if row["原价"] != _display_original_price(record):
            raise ExportValidationError(f"ZH_EXPORT_ORIGINAL_PRICE_MISMATCH: {sku}")
        if _text_or_none(row["图片链接"]) != _text_or_none(record.get("image_url")):
            raise ExportValidationError(f"ZH_EXPORT_IMAGE_URL_MISMATCH: {sku}")
        if _text(row["商品链接"]) != _text(record.get("product_url")):
            raise ExportValidationError(f"ZH_EXPORT_PRODUCT_URL_MISMATCH: {sku}")


def canonical_source_hash(records: Iterable[dict[str, Any]]) -> str:
    normalized = []
    for record in sorted(records, key=_sku_sort_key):
        normalized.append({field: _canonical_value(record.get(field)) for field in _SOURCE_HASH_FIELDS})
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def canonical_export_rows_hash(rows: Iterable[dict[str, Any]]) -> str:
    """Hash the stable, publishable Chinese projection rather than SKU count."""
    normalized: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: str(item.get("编号") or "")):
        normalized.append({column: _hash_cell_value(row.get(column)) for column in _HASHED_ZH_COLUMNS})
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _hash_cell_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return format(float(value), ".15g")
    text = str(value).strip()
    # openpyxl materializes an empty string cell as None on read-back.  Both
    # representations mean an empty export field and must hash identically.
    return text or None


def _release_mode(value: str) -> str:
    mode = str(value or PREVIEW).strip().upper()
    if mode not in {PREVIEW, PRODUCTION_RELEASE}:
        raise ExportValidationError(f"EXPORT_RELEASE_MODE_INVALID: {value}")
    return mode


def _verify_written_workbook(path: Path, *, headers: list[str], expected_skus: set[str],
                             expect_images: bool = False, expected_image_count: int = 0) -> None:
    workbook = openpyxl.load_workbook(path, read_only=False, data_only=True)
    try:
        if workbook.sheetnames != ["商品全量"]:
            raise ExportValidationError("EXPORT_XLSX_SHEET_MISMATCH")
        ws = workbook["商品全量"]
        actual_headers = [cell.value for cell in ws[1]]
        if actual_headers != headers or ws.freeze_panes != "A2":
            raise ExportValidationError("EXPORT_XLSX_HEADER_OR_FREEZE_MISMATCH")
        if ws.auto_filter.ref != f"A1:N{len(expected_skus) + 1}":
            raise ExportValidationError("EXPORT_XLSX_FILTER_MISMATCH")
        sku_column = headers.index("编号") + 1
        actual_skus = {str(ws.cell(row=row, column=sku_column).value or "").strip() for row in range(2, ws.max_row + 1)}
        if actual_skus != expected_skus or len(actual_skus) != len(expected_skus):
            raise ExportValidationError("EXPORT_XLSX_SKU_SET_MISMATCH")
        if expect_images and len(getattr(ws, "_images", ())) != expected_image_count:
            raise ExportValidationError("EXPORT_XLSX_IMAGE_COUNT_MISMATCH")
    finally:
        workbook.close()


def _read_catalog_rows(path: Path, headers: list[str]) -> list[dict[str, Any]]:
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = workbook["商品全量"]
        actual_headers = [cell.value for cell in ws[1]]
        if actual_headers != headers:
            raise ExportValidationError("EXPORT_XLSX_HEADER_OR_FREEZE_MISMATCH")
        return [
            {str(header): values[index] for index, header in enumerate(headers)}
            for values in ws.iter_rows(min_row=2, values_only=True)
        ]
    finally:
        workbook.close()


def _read_csv_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _resolve_image_eligibility(
    cfg: dict[str, Any], records: Iterable[dict[str, Any]], image_root: Path,
) -> dict[str, bool]:
    """Resolve current-image eligibility without network access.

    A derivative is exportable only when the manifest says the current URL is
    available and its cache metadata is bound to the current master hash.
    Presence of a PNG file by itself is deliberately insufficient.
    """
    from ..images.assets import ImageManifest
    from ..images.derivatives import ImageDerivativeService

    image_cfg = cfg.get("images") or {}
    raw_manifest = image_cfg.get("manifest_path")
    manifest_path = Path(str(raw_manifest)) if raw_manifest else Path(cfg["paths"]["images"]) / "manifests" / "image_manifest.csv"
    if not manifest_path.is_absolute():
        manifest_path = Path(cfg["project_root"]) / manifest_path
    manifest = ImageManifest(manifest_path)
    eligibility: dict[str, bool] = {}
    for record in records:
        sku = str(record.get("sku") or "").strip()
        current_url = str(record.get("image_url") or "").strip()
        asset = manifest.records.get(sku)
        eligible = bool(asset and current_url and asset.source_image_url == current_url and asset.available)
        if eligible:
            master = Path(asset.master_image_path)
            if not master.is_absolute():
                master = Path(cfg["project_root"]) / master
            derivative = image_root / f"{sku}.png"
            metadata = derivative.with_suffix(".json")
            try:
                master_hash = hashlib.sha256(master.read_bytes()).hexdigest()
                cached = json.loads(metadata.read_text(encoding="utf-8"))
                expected_key = ImageDerivativeService.cache_key(master_hash, "excel_250_white_v1")
                with Image.open(derivative) as image:
                    image.load()
                    valid_png = image.format == "PNG" and image.size == (250, 250) and image.mode == "RGB"
                eligible = valid_png and asset.master_hash == master_hash and cached.get("cache_key") == expected_key
            except (OSError, ValueError, TypeError, UnidentifiedImageError, json.JSONDecodeError):
                eligible = False
        eligibility[sku] = eligible
    return eligibility


_DETAIL_EXPORT_FIELDS = ("name_es", "cat1_es", "cat2_es", "spec_es", "desc_es", "details_es", "product_url", "image_url")


def _detail_retry_ids(source: ExportSource) -> list[str]:
    """返回来源快照中实际存在的详情补抓目录，供 manifest 追溯。"""
    if source.directory is None:
        return []
    return [path.name for path in _iter_valid_detail_retry_dirs(source.directory)]


def _iter_valid_detail_retry_dirs(directory: Path) -> list[Path]:
    """只接受有完整成功报告的详情补抓目录。

    目录里可能同时保留失败/中断的历史尝试；这些尝试不能覆盖正式快照，
    否则会把不完整详情误当成已验证事实。
    """
    retry_root = directory / "detail_retries"
    if not retry_root.exists():
        return []
    valid: list[Path] = []
    for retry_dir in sorted(path for path in retry_root.iterdir() if path.is_dir()):
        report_path = retry_dir / "detail_retry_report.json"
        checkpoint = retry_dir / "detail_fetch.jsonl"
        if not report_path.exists() or not checkpoint.exists():
            continue
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(report, dict):
            continue
        planned = report.get("planned")
        completed = report.get("completed")
        pending_after = report.get("pending_after")
        if not bool(report.get("detail_retry_pass")):
            continue
        if planned is None or completed is None or pending_after is None:
            continue
        try:
            complete = int(planned) == int(completed) and int(pending_after) == 0
        except (TypeError, ValueError):
            complete = False
        if complete:
            valid.append(retry_dir)
    return valid


def _merge_detail_retries(directory: Path, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """将已落盘的 detail-retry 证据合并到历史快照的商品事实。"""
    retry_dirs = _iter_valid_detail_retry_dirs(directory)
    if not retry_dirs:
        return records
    by_sku = {str(row.get("sku") or ""): row for row in records}
    for retry_dir in retry_dirs:
        checkpoint = retry_dir / "detail_fetch.jsonl"
        if not checkpoint.exists():
            continue
        try:
            lines = checkpoint.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                payload = json.loads(line)
                sku = str(payload.get("sku") or "")
                detail = payload.get("detail") or {}
            except (json.JSONDecodeError, TypeError):
                continue
            if sku not in by_sku or not isinstance(detail, dict):
                continue
            for field in _DETAIL_EXPORT_FIELDS:
                value = detail.get(field)
                if value not in (None, ""):
                    by_sku[sku][field] = value
    return records


def _display_original_price(record: dict[str, Any]) -> float | None:
    current = _required_price(record.get("current_price"), sku=str(record.get("sku")), field="current_price")
    original = _optional_price(record.get("original_price"), sku=str(record.get("sku")), field="original_price")
    return original if original is not None and original > current else None


def _required_price(value: Any, *, sku: str, field: str) -> float:
    parsed = _optional_price(value, sku=sku, field=field)
    if parsed is None or not math.isfinite(parsed):
        raise ExportValidationError(f"EXPORT_SOURCE_BAD_PRICE: {sku}/{field}")
    return parsed


def _optional_price(value: Any, *, sku: str, field: str) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = parse_price(value)
    except (TypeError, ValueError) as exc:
        raise ExportValidationError(f"EXPORT_SOURCE_BAD_PRICE: {sku}/{field}") from exc
    return None if parsed is None else float(parsed)


def _es_remarks(record: dict[str, Any]) -> str:
    values = ["Estado: CURRENT"]
    if parse_bool_zh(record.get("is_new_badge")):
        values.append("Nuevo")
    if parse_bool_zh(record.get("promotion")):
        values.append("Promoción")
    if parse_bool_zh(record.get("sustainable")):
        values.append("Sostenible")
    discount = _optional_price(record.get("discount"), sku=str(record.get("sku")), field="discount")
    if discount is not None:
        values.append(f"Descuento: {discount:g}")
    raw_tags = _text_or_none(record.get("raw_tags"))
    if raw_tags:
        values.append(f"Etiquetas oficiales: {raw_tags}")
    # Keep source omissions visible in the Spanish projection.  Do not invent
    # detail/category facts or silently turn an incomplete observation into a
    # complete row; the labels make the gap auditable for later detail retry.
    if not _text_or_none(record.get("desc_es")):
        values.append("Descripción pendiente")
    if not _text_or_none(record.get("details_es")):
        values.append("Detalles pendientes")
    if not _text_or_none(record.get("cat2_es")):
        values.append("Categoría 2 pendiente")
    return "；".join(values)


def _sku_sort_key(record: dict[str, Any]) -> tuple[int, int, str]:
    sku = str(record.get("sku") or "").strip()
    if sku.isdigit():
        return (0, int(sku), sku)
    return (1, 0, sku)


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _validate_date(value: str) -> None:
    try:
        dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ExportValidationError(f"EXPORT_DATE_INVALID: {value}") from exc


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile_mkstemp(path)
    os.close(fd)
    temp_path = Path(temporary)
    try:
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _publish_export_pair(
    preview_path: Path,
    output_path: Path,
    manifest_path: Path,
    manifest: dict[str, Any],
) -> None:
    """Compatibility wrapper for the legacy ES workbook/manifest pair."""
    _publish_export_bundle(preview_path, output_path, manifest_path, manifest)


def _publish_export_bundle(
    preview_path: Path,
    output_path: Path,
    manifest_path: Path,
    manifest: dict[str, Any],
    *,
    repair_report_path: Path | None = None,
    repair_report: dict[str, Any] | None = None,
) -> None:
    """Atomically publish xlsx, manifest and optional Chinese repair report."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if (repair_report_path is None) != (repair_report is None):
        raise ExportValidationError("EXPORT_REPAIR_BUNDLE_INCOMPLETE")
    generated: list[tuple[Path, Path]] = [(preview_path, output_path)]
    manifest_tmp: Path | None = None
    try:
        fd, manifest_name = tempfile.mkstemp(prefix=f".{manifest_path.stem}.", suffix=".tmp", dir=output_path.parent)
        os.close(fd)
        manifest_tmp = Path(manifest_name)
        manifest_tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        generated.append((manifest_tmp, manifest_path))
        if repair_report_path is not None and repair_report is not None:
            fd, repair_name = tempfile.mkstemp(prefix=f".{repair_report_path.stem}.", suffix=".tmp", dir=output_path.parent)
            os.close(fd)
            repair_tmp = Path(repair_name)
            repair_tmp.write_text(json.dumps(repair_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            generated.append((repair_tmp, repair_report_path))
        backups: dict[Path, Path | None] = {}
        for _, target in generated:
            if not target.exists():
                backups[target] = None
                continue
            fd, backup_name = tempfile.mkstemp(prefix=f".{target.stem}.old.", suffix=target.suffix, dir=output_path.parent)
            os.close(fd)
            backup = Path(backup_name)
            try:
                shutil.copy2(target, backup)
            except BaseException:
                backup.unlink(missing_ok=True)
                raise
            backups[target] = backup
        replaced: list[Path] = []
        try:
            for temporary, target in generated:
                temporary.replace(target)
                replaced.append(target)
        except BaseException:
            for target in reversed(replaced):
                prior = backups[target]
                if prior is None:
                    target.unlink(missing_ok=True)
                else:
                    prior.replace(target)
            raise
    except BaseException:
        raise
    finally:
        for temporary, _ in generated:
            if temporary.exists():
                temporary.unlink()
        for backup in locals().get("backups", {}).values():
            if backup and backup.exists():
                backup.unlink()


def _sku_set_hash(skus: Iterable[str]) -> str:
    payload = "\n".join(sorted(str(sku) for sku in skus)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _is_truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def tempfile_mkstemp(path: Path) -> tuple[int, str]:
    import tempfile
    return tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent)


def _canonical_value(value: Any) -> str | float | int | bool | None:
    if value is None:
        return None
    if isinstance(value, (bool, int, float)):
        return value
    return str(value).strip()


def _text(value: Any) -> str:
    return _clean_display_text(value) or ""


def _text_or_none(value: Any) -> str | None:
    return _clean_display_text(value) or None


def _clean_display_text(value: Any) -> str:
    """Remove parser sentinels from the export projection only.

    Raw/normalized source facts remain untouched.  A few legacy detail
    payloads serialized a missing leading value as the literal ``null`` (or
    ``undefined``), sometimes followed by a real sentence.  Exports must not
    publish that sentinel as Spanish content, but must preserve the text that
    follows it.
    """
    if value is None:
        return ""
    text = str(value).strip()
    lowered = text.casefold()
    for sentinel in ("null", "undefined"):
        if lowered == sentinel:
            return ""
        prefix = sentinel + "."
        if lowered.startswith(prefix):
            text = text[len(prefix):].lstrip()
            break
    # Historical descriptions can contain copied or malformed HTML fragments.
    # Clean only the export projection; raw/normalized evidence stays intact.
    if re.search(r"</?\w|>\s*>", text):
        text = html.unescape(text)
        def _tag_replacement(match: re.Match[str]) -> str:
            tag = match.group(0).casefold()
            return "\n" if tag.startswith(("<p", "</p", "<div", "</div", "<br")) else ""

        text = re.sub(r"<[^>]*>", _tag_replacement, text)
        text = text.replace(">", "").replace("<", "")
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text
