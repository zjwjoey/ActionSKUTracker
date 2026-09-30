"""每日证据文件：Snapshot 与 Staging（规范 §38/§39）。

Snapshot: runtime/snapshots/<run_date>/  机器证据，只新增不改旧。
Staging:  runtime/staging/<run_id>/      正式写入 Master 前的暂存区。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
from typing import Any


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


def _write_text_atomic(path: Path, text: str) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)


def _write_csv(path: Path, rows: list[dict], fieldnames: list[str] | None = None) -> None:
    if not rows:
        if fieldnames:
            with path.open("w", encoding="utf-8-sig", newline="") as f:
                csv.DictWriter(f, fieldnames=fieldnames).writeheader()
        else:
            path.write_text("", encoding="utf-8-sig")
        return
    headers = fieldnames or list(rows[0].keys())
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


_SOURCE_ANOMALY_COLUMNS = [
    "qa_id", "sku", "evidence_index", "anomaly_code", "source_hash", "source_field", "source_key",
    "source_value", "source_fields_json", "rules_manifest_json", "status", "action",
]


def _source_anomaly_rows(qa_report: dict[str, Any]) -> list[dict[str, str]]:
    """Project source-only QA findings into a stable, separately reviewable ledger."""
    rows: list[dict[str, str]] = []
    for finding in qa_report.get("findings") or []:
        if not isinstance(finding, dict) or finding.get("finding_type") != "SOURCE_ANOMALY_OR_CONFLICT":
            continue
        evidence_items = finding.get("evidence") or []
        if not evidence_items:
            evidence_items = [{"code": code} for code in finding.get("flags") or []]
        for evidence_index, evidence in enumerate(evidence_items, start=1):
            if not isinstance(evidence, dict):
                continue
            code = str(evidence.get("code") or "SOURCE_ANOMALY_UNSPECIFIED")
            identity = {
                "sku": str(finding.get("sku") or ""),
                "evidence_index": evidence_index,
                "source_hash": str(finding.get("source_hash") or ""),
                "anomaly_code": code,
                "source_field": str(evidence.get("source_field") or ""),
                "source_key": str(evidence.get("source_key") or ""),
                "source_value": str(evidence.get("source_value") or ""),
            }
            rows.append({
                "qa_id": hashlib.sha256(
                    ("SOURCE_ANOMALY_V1|" + json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))).encode("utf-8")
                ).hexdigest(),
                **{key: str(value) for key, value in identity.items()},
                "source_fields_json": json.dumps(finding.get("source_fields") or {}, ensure_ascii=False, sort_keys=True),
                "rules_manifest_json": json.dumps(finding.get("source_consistency_rules") or {}, ensure_ascii=False, sort_keys=True),
                "status": str(finding.get("status") or "OPEN_REVIEW"),
                "action": str(finding.get("action") or "REVIEW_ONLY_SOURCE_UNCHANGED"),
            })
    return sorted(rows, key=lambda row: (
        row["sku"], row["anomaly_code"], row["source_field"], row["source_key"],
        row["evidence_index"], row["qa_id"],
    ))


def verify_source_anomaly_manifest(snapshot_dir: Path) -> dict[str, Any]:
    """Verify the snapshot anomaly ledger and its binding to qa_report.json."""
    snapshot_dir = Path(snapshot_dir)
    manifest_path = snapshot_dir / "SOURCE_ANOMALY.manifest.json"
    issues: list[str] = []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"passed": False, "issues": ["SOURCE_ANOMALY_MANIFEST_UNREADABLE"], "row_count": None}
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "source-anomaly-v1":
        return {"passed": False, "issues": ["SOURCE_ANOMALY_MANIFEST_SCHEMA_INVALID"], "row_count": None}
    if manifest.get("artifact") != "SOURCE_ANOMALY.csv" or manifest.get("qa_report_artifact") != "qa_report.json":
        return {"passed": False, "issues": ["SOURCE_ANOMALY_MANIFEST_ARTIFACT_INVALID"], "row_count": None}

    anomaly_path = snapshot_dir / "SOURCE_ANOMALY.csv"
    qa_report_path = snapshot_dir / "qa_report.json"
    try:
        anomaly_bytes = anomaly_path.read_bytes()
        qa_report_bytes = qa_report_path.read_bytes()
    except OSError:
        return {"passed": False, "issues": ["SOURCE_ANOMALY_ARTIFACT_MISSING"], "row_count": None}
    if hashlib.sha256(anomaly_bytes).hexdigest() != manifest.get("sha256"):
        issues.append("SOURCE_ANOMALY_HASH_MISMATCH")
    if hashlib.sha256(qa_report_bytes).hexdigest() != manifest.get("qa_report_sha256"):
        issues.append("SOURCE_ANOMALY_QA_REPORT_HASH_MISMATCH")

    try:
        reader = csv.DictReader(io.StringIO(anomaly_bytes.decode("utf-8-sig"), newline=""))
        actual_headers = reader.fieldnames
        rows = list(reader)
    except (UnicodeDecodeError, csv.Error):
        actual_headers, rows = None, []
        issues.append("SOURCE_ANOMALY_CSV_INVALID")
    if actual_headers != _SOURCE_ANOMALY_COLUMNS:
        issues.append("SOURCE_ANOMALY_CSV_SCHEMA_INVALID")
    qa_ids = [row.get("qa_id", "") for row in rows]
    if any(not qa_id for qa_id in qa_ids) or len(set(qa_ids)) != len(qa_ids):
        issues.append("SOURCE_ANOMALY_IDS_INVALID")
    if len(rows) != manifest.get("row_count"):
        issues.append("SOURCE_ANOMALY_ROW_COUNT_MISMATCH")
    return {
        "passed": not issues,
        "issues": sorted(set(issues)),
        "row_count": len(rows),
        "manifest": manifest,
    }


def _dicts_from_objs(objs: list[Any], fields: list[str]) -> list[dict]:
    out = []
    for o in objs:
        d = getattr(o, "__dict__", {})
        if isinstance(o, dict):
            d = o
        out.append({k: d.get(k) for k in fields})
    return out


def write_snapshot(cfg: dict[str, Any], run_date: str, data: dict[str, Any]) -> Path:
    """写入每日 snapshot 目录，返回目录路径。"""
    run_id = (data.get("run_report") or {}).get("run_id")
    if not run_id:
        raise ValueError("snapshot 需要 run_report.run_id，避免同日运行覆盖证据")
    snap_dir: Path = cfg["paths"]["snapshots"] / run_date / str(run_id)
    snap_dir.mkdir(parents=True, exist_ok=True)

    if data.get("sitemap_raw_xml"):
        _write_text_atomic(snap_dir / "sitemap_raw.xml", data["sitemap_raw_xml"])
    if data.get("sitemap_skus"):
        _write_csv(snap_dir / "sitemap_skus.csv", [{"sku": s} for s in data["sitemap_skus"]])
    if data.get("listing_raw"):
        _write_text_atomic(snap_dir / "listing_raw.json", _json(data["listing_raw"]))
    if data.get("listing_products"):
        _write_csv(snap_dir / "listing_products.csv", data["listing_products"])
    if data.get("products_normalized"):
        _write_csv(snap_dir / "products_normalized.csv", data["products_normalized"])
    if data.get("sku_delta"):
        _write_csv(snap_dir / "sku_delta.csv", data["sku_delta"])
    if data.get("presence_evidence"):
        _write_csv(snap_dir / "presence_evidence.csv", data["presence_evidence"])
    if data.get("coverage") is not None:
        _write_text_atomic(snap_dir / "coverage.json", _json(data["coverage"]))
    if data.get("site_structure") is not None:
        _write_text_atomic(snap_dir / "site_structure.json", _json(data["site_structure"]))
        _write_csv(snap_dir / "category_structure.csv", data["site_structure"].get("categories") or [])
    if data.get("run_manifest") is not None:
        _write_text_atomic(snap_dir / "run_manifest.json", _json(data["run_manifest"]))
    if data.get("detail_evidence"):
        _write_csv(snap_dir / "detail_evidence.csv", data["detail_evidence"])
    if data.get("detail_backlog"):
        _write_csv(snap_dir / "detail_backlog.csv", data["detail_backlog"])
    if data.get("product_updates"):
        _write_csv(snap_dir / "product_updates.csv", data["product_updates"])
    if data.get("translation_updates"):
        _write_csv(snap_dir / "translation_updates.csv", data["translation_updates"])
    if data.get("qa_report"):
        qa_report_text = _json(data["qa_report"])
        _write_text_atomic(snap_dir / "qa_report.json", qa_report_text)
        anomaly_rows = _source_anomaly_rows(data["qa_report"])
        anomaly_path = snap_dir / "SOURCE_ANOMALY.csv"
        qa_report_path = snap_dir / "qa_report.json"
        _write_csv(
            anomaly_path,
            anomaly_rows,
            _SOURCE_ANOMALY_COLUMNS,
        )
        anomaly_manifest = {
            "schema_version": "source-anomaly-v1",
            "artifact": anomaly_path.name,
            "row_count": len(anomaly_rows),
            "sha256": hashlib.sha256(anomaly_path.read_bytes()).hexdigest(),
            "qa_report_artifact": qa_report_path.name,
            "qa_report_sha256": hashlib.sha256(qa_report_path.read_bytes()).hexdigest(),
        }
        _write_text_atomic(snap_dir / "SOURCE_ANOMALY.manifest.json", _json(anomaly_manifest))
        anomaly_verification = verify_source_anomaly_manifest(snap_dir)
        if not anomaly_verification["passed"]:
            raise RuntimeError(
                "SNAPSHOT_SOURCE_ANOMALY_INTEGRITY_FAIL:"
                + ",".join(anomaly_verification["issues"])
            )
    if data.get("run_report"):
        _write_text_atomic(snap_dir / "run_report.json", _json(data["run_report"]))
    return snap_dir


def write_staging(cfg: dict[str, Any], run_id: str, data: dict[str, Any]) -> Path:
    """写入 staging 暂存区，返回目录路径。"""
    stage_dir: Path = cfg["paths"]["staging"] / run_id
    stage_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in (
        ("sku_changes.csv", data.get("sku_changes")),
        ("product_changes.csv", data.get("product_changes")),
        ("price_changes.csv", data.get("price_changes")),
        ("translation_changes.csv", data.get("translation_changes")),
        ("event_changes.csv", data.get("event_changes")),
        ("presence_evidence.csv", data.get("presence_evidence")),
        ("lifecycle_changes.csv", data.get("lifecycle_changes")),
    ):
        if rows:
            _write_csv(stage_dir / name, rows)
    return stage_dir
