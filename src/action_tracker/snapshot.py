"""每日证据文件：Snapshot 与 Staging（规范 §38/§39）。

Snapshot: runtime/snapshots/<run_date>/  机器证据，只新增不改旧。
Staging:  runtime/staging/<run_id>/      正式写入 Master 前的暂存区。
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any


def _write_json_atomic(path: Path, obj: Any, *, compact: bool = False) -> None:
    """Stream evidence to the existing atomic path without a full JSON copy."""
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        json.dump(obj, stream, ensure_ascii=False, default=str,
                  indent=None if compact else 2,
                  separators=(",", ":") if compact else None)
    temp.replace(path)


def _write_text_atomic(path: Path, text: str) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)


def _file_hash(path: Path) -> bytes:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').digest()


def _write_csv(path: Path, rows: list[dict], *, duplicate_sources=()) -> None:
    """Publish complete bytes atomically, optionally sharing identical run views.

    Every CSV rewrite replaces the directory entry, so a staging rewrite cannot
    mutate a linked Snapshot. Unsupported/cross-volume links retain ordinary
    files. Only explicitly bound, same-run candidates are passed by callers.
    """
    temp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    link = temp.with_suffix('.link')
    try:
        with temp.open('x', encoding='utf-8-sig', newline='') as stream:
            if rows:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]), extrasaction='ignore')
                writer.writeheader()
                writer.writerows(rows)
            else:
                stream.write('')  # Preserve the legacy empty UTF-8 BOM bytes.
        digest = None
        for source in duplicate_sources:
            source = Path(source)
            if source.is_symlink() or not source.is_file() or source.stat().st_size != temp.stat().st_size:
                continue
            digest = digest or _file_hash(temp)
            if _file_hash(source) != digest:
                continue
            try:
                os.link(source, link)
                # Check the linked inode, rather than a path that could change.
                if _file_hash(link) == digest:
                    link.replace(temp)
                    break
            except OSError:
                pass  # Retain complete independent bytes when links are unavailable.
            finally:
                link.unlink(missing_ok=True)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
        link.unlink(missing_ok=True)


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
        _write_json_atomic(snap_dir / "listing_raw.json", data["listing_raw"], compact=True)
    if data.get("listing_products"):
        _write_csv(snap_dir / "listing_products.csv", data["listing_products"])
    if data.get("products_normalized"):
        _write_csv(snap_dir / "products_normalized.csv", data["products_normalized"])
    if data.get("sku_delta"):
        _write_csv(snap_dir / "sku_delta.csv", data["sku_delta"])
    if data.get("presence_evidence"):
        _write_csv(snap_dir / "presence_evidence.csv", data["presence_evidence"])
    if data.get("coverage") is not None:
        _write_json_atomic(snap_dir / "coverage.json", data["coverage"])
    if data.get("site_structure") is not None:
        _write_json_atomic(snap_dir / "site_structure.json", data["site_structure"])
        _write_csv(snap_dir / "category_structure.csv", data["site_structure"].get("categories") or [])
    if data.get("run_manifest") is not None:
        _write_json_atomic(snap_dir / "run_manifest.json", data["run_manifest"])
    if data.get("detail_evidence"):
        _write_csv(snap_dir / "detail_evidence.csv", data["detail_evidence"])
    if data.get("product_updates"):
        _write_csv(snap_dir / "product_updates.csv", data["product_updates"])
    if data.get("translation_updates"):
        _write_csv(snap_dir / "translation_updates.csv", data["translation_updates"])
    if data.get("qa_report"):
        _write_json_atomic(snap_dir / "qa_report.json", data["qa_report"])
    if data.get("run_report"):
        _write_json_atomic(snap_dir / "run_report.json", data["run_report"])
    return snap_dir


def write_staging(cfg: dict[str, Any], run_id: str, data: dict[str, Any], *,
                  snapshot_dir: Path | None = None) -> Path:
    """写入 staging 暂存区，返回目录路径。"""
    stage_dir: Path = cfg["paths"]["staging"] / run_id
    stage_dir.mkdir(parents=True, exist_ok=True)
    # No discovery of old snapshots by matching content or guessed dates.
    # A supplied path must belong to this exact run and configured authority.
    if snapshot_dir is not None:
        root = Path(cfg['paths']['snapshots']).resolve()
        snapshot_dir = Path(snapshot_dir)
        resolved = snapshot_dir.resolve()
        if (snapshot_dir.is_symlink() or not resolved.is_relative_to(root)
                or resolved == root or resolved.name != run_id):
            raise ValueError('staging snapshot identity mismatch')
        report = json.loads((resolved / 'run_report.json').read_text(encoding='utf-8'))
        if report.get('run_id') != run_id:
            raise ValueError('staging snapshot run mismatch')
        snapshot_dir = resolved
    snapshot_names = {'sku_changes.csv': 'sku_delta.csv',
                      'lifecycle_changes.csv': 'sku_delta.csv',
                      'product_changes.csv': 'product_updates.csv',
                      'translation_changes.csv': 'translation_updates.csv',
                      'presence_evidence.csv': 'presence_evidence.csv'}
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
            candidates = []
            if snapshot_dir is not None and name in snapshot_names:
                candidates.append(snapshot_dir / snapshot_names[name])
            if name == 'lifecycle_changes.csv':
                candidates.append(stage_dir / 'sku_changes.csv')
            _write_csv(stage_dir / name, rows, duplicate_sources=candidates)
    return stage_dir
