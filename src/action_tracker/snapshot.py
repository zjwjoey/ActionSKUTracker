"""每日证据文件：Snapshot 与 Staging（规范 §38/§39）。

Snapshot: runtime/snapshots/<run_date>/  机器证据，只新增不改旧。
Staging:  runtime/staging/<run_id>/      正式写入 Master 前的暂存区。
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    headers = list(rows[0].keys())
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


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
        (snap_dir / "sitemap_raw.xml").write_text(data["sitemap_raw_xml"], encoding="utf-8")
    if data.get("sitemap_skus"):
        _write_csv(snap_dir / "sitemap_skus.csv", [{"sku": s} for s in data["sitemap_skus"]])
    if data.get("listing_raw"):
        (snap_dir / "listing_raw.json").write_text(_json(data["listing_raw"]), encoding="utf-8")
    if data.get("listing_products"):
        _write_csv(snap_dir / "listing_products.csv", data["listing_products"])
    if data.get("products_normalized"):
        _write_csv(snap_dir / "products_normalized.csv", data["products_normalized"])
    if data.get("sku_delta"):
        _write_csv(snap_dir / "sku_delta.csv", data["sku_delta"])
    if data.get("presence_evidence"):
        _write_csv(snap_dir / "presence_evidence.csv", data["presence_evidence"])
    if data.get("coverage") is not None:
        (snap_dir / "coverage.json").write_text(_json(data["coverage"]), encoding="utf-8")
    if data.get("product_updates"):
        _write_csv(snap_dir / "product_updates.csv", data["product_updates"])
    if data.get("translation_updates"):
        _write_csv(snap_dir / "translation_updates.csv", data["translation_updates"])
    if data.get("qa_report"):
        (snap_dir / "qa_report.json").write_text(_json(data["qa_report"]), encoding="utf-8")
    if data.get("run_report"):
        (snap_dir / "run_report.json").write_text(_json(data["run_report"]), encoding="utf-8")
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
