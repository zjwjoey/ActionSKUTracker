"""Source-bound weekly history; original full catalogs are the sole 0/1 authority."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import openpyxl

from .history import HistoryExportError, HistorySourceStat, PresenceHistory, _parse_iso_date, _resolve_path, _read_workbook

MODE = "weekly_raw_catalog_v2"


def _error(code: str, detail: Any) -> None:
    raise HistoryExportError(f"HISTORY_WEEKLY_{code}: {detail}")


def _hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _catalog(path: Path, source: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], str]:
    if not path.is_file():
        _error("SOURCE_MISSING", path)
    before = _hash(path)
    if source.get("sha256") and source["sha256"] != before:
        _error("SOURCE_HASH_CHANGED", path)
    for key in ("presence_capability", "absence_capability", "observation_complete"):
        if source.get(key) is not True:
            _error("SOURCE_INCOMPLETE", f"{path}/{key}")
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        matches = []
        for sheet in workbook:
            if source.get("sheet") and sheet.title != source["sheet"]:
                continue
            iterator = iter(sheet.values)
            headers = list(next(iterator, ()))
            if "编号" in headers:
                matches.append((sheet.title, headers, iterator))
        if len(matches) != 1:
            _error("SHEET_AMBIGUOUS", path)
        sheet, headers, iterator = matches[0]
        if any(re.fullmatch(r"\d{2}\.\d{2}\.\d{2}", str(h or "")) for h in headers):
            _error("MATRIX_NOT_RAW_CATALOG", path)
        index = headers.index("编号")
        records = {}
        fields = {
            "name_zh": ("中文品名", "标题（中文）", "标题", "品名", "标题（商品名）"),
            "image_url": ("图片链接",), "product_url": ("商品链接",),
        }
        for line, row in enumerate(iterator, 2):
            if not any(v not in (None, "") for v in row):
                continue
            value = row[index] if index < len(row) else None
            sku = str(value).strip() if value is not None else ""
            if not re.fullmatch(r"[0-9]+", sku):
                _error("SKU_INVALID", f"{path}:{line}")
            if sku in records:
                _error("SKU_DUPLICATE", f"{path}/{sku}")
            record = {}
            for field, candidates in fields.items():
                for header in candidates:
                    if header in headers:
                        pos = headers.index(header)
                        val = row[pos] if pos < len(row) else None
                        if val not in (None, ""):
                            record[field] = val
                            break
            url = str(record.get("product_url") or "")
            match = re.search(r"/p/([0-9]+)(?:/|$)", url)
            if not match or match[1] != sku:
                _error("PRODUCT_LINK_SKU_MISMATCH", f"{path}/{sku}")
            records[sku] = record
        if not records:
            _error("SOURCE_EMPTY", path)
        count = source.get('expected_sku_count')
        if (not re.fullmatch(r'[a-f0-9]{64}', str(source.get('sha256') or ''))
                or not isinstance(count, int) or isinstance(count, bool) or count < 1):
            _error('SOURCE_IDENTITY_MISSING', path)
        if source.get("expected_sku_count") is not None and len(records) != source["expected_sku_count"]:
            _error("SOURCE_COUNT_CHANGED", path)
    finally:
        workbook.close()
    if _hash(path) != before:
        _error("SOURCE_CHANGED_DURING_READ", path)
    return records, before


def load_weekly_history(cfg: dict[str, Any], raw: dict[str, Any], *, as_of_date: str | None) -> PresenceHistory:
    if raw.get("profile_version") != 2:
        _error("PROFILE_VERSION_INVALID", raw.get("profile_version"))
    sources = list(raw.get("sources") or [])
    if not sources:
        _error("SOURCES_EMPTY", "register original full catalogs")
    excluded = frozenset(str(s) for s in raw.get("excluded_skus", []))
    exceptions = set(raw.get("baseline_dates") or [])
    cutoff = _parse_iso_date(as_of_date) if as_of_date else None
    dates_seen = set()
    for source in sources:
        date = _parse_iso_date(source.get("date"))
        if date in dates_seen:
            _error("PERIOD_DUPLICATE", date)
        dates_seen.add(date)
        if dt.date.fromisoformat(date).weekday() != 0 and date not in exceptions:
            _error("PERIOD_NOT_MONDAY", date)
    discovery = raw.get("discover") or {}
    if discovery.get("enabled") is True:
        root = _resolve_path(cfg, discovery.get("root"))
        if not root.is_dir():
            _error("DISCOVERY_ROOT_MISSING", root)
        floor = max(dates_seen)
        for folder in sorted(root.iterdir()):
            if not folder.is_dir() or not re.fullmatch(r"[0-9]{8}", folder.name):
                continue
            try:
                date = dt.datetime.strptime(folder.name, "%Y%m%d").date().isoformat()
            except ValueError:
                continue
            if date <= floor or (cutoff and date > cutoff) or dt.date.fromisoformat(date).weekday() != 0:
                continue
            candidates = [p for p in folder.glob("*.xlsx") if re.fullmatch(
                folder.name + r"Action(?:商品全量_中文版_不带图|今日在售SKU_中文不带图)\.xlsx", p.name)]
            if len(candidates) != 1:
                _error("SOURCE_AMBIGUOUS", folder)
            # Naming and inherited booleans do not prove completeness. A new
            # period needs an independently registered count/hash and explicit
            # completion receipt before it can create negative evidence.
            receipt_path = candidates[0].with_suffix('.weekly-source.json')
            if not receipt_path.is_file():
                _error("COMPLETENESS_RECEIPT_MISSING", candidates[0])
            try:
                receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
            except (ValueError, OSError) as exc:
                _error("COMPLETENESS_RECEIPT_INVALID", str(exc))
            count = receipt.get('expected_sku_count')
            if (receipt.get('contract') != 'ACTION_WEEKLY_CATALOG_SOURCE_V1'
                    or receipt.get('date') != date or receipt.get('observation_complete') is not True
                    or receipt.get('qa_state') != 'PASS' or receipt.get('commit_status') != 'FULL_COMMIT'
                    or receipt.get('authorized_by') != 'human:owner'
                    or not isinstance(count, int) or isinstance(count, bool) or count < 1
                    or not re.fullmatch(r'[a-f0-9]{64}', str(receipt.get('sha256') or ''))):
                _error("COMPLETENESS_RECEIPT_INVALID", receipt_path)
            sources.append({**discovery, "path": str(candidates[0]), "date": date,
                            'sha256': receipt['sha256'], 'expected_sku_count': count})
    selected = [s for s in sources if cutoff is None or s["date"] <= cutoff]
    if not selected:
        _error("PERIODS_EMPTY", cutoff)
    weekly = sorted(s["date"] for s in selected if s["date"] not in exceptions)
    if not weekly:
        _error("MONDAY_PERIODS_EMPTY", cutoff)
    end = dt.date.fromisoformat(cutoff or weekly[-1])
    end -= dt.timedelta(days=end.weekday())
    start = dt.date.fromisoformat(weekly[0])
    expected = set()
    while start <= end:
        expected.add(start.isoformat())
        start += dt.timedelta(days=7)
    if expected != set(weekly):
        _error("PERIOD_GAP", sorted(expected - set(weekly)))
    catalogs, latest, stats, last_observed = {}, {}, [], {}
    for source in sorted(selected, key=lambda s: s["date"]):
        date = source["date"]
        path = _resolve_path(cfg, source.get("path"))
        records, digest = _catalog(path, source)
        catalogs[date] = set(records) - excluded
        for sku, fields in records.items():
            if sku not in excluded:
                target = latest.setdefault(sku, {})
                target.update(fields)
                last_observed[sku] = date
        stats.append(HistorySourceStat(date, str(path), len(records), len(records), 0,
                                       True, True, True, "A", f"COMPLETE_RAW_CATALOG_SHA256:{digest}"))
    # Reviewed display fields are separate from presence authority. Never read
    # date/status cells from this table, or add its SKUs to the source union.
    display = raw.get("display_source") or {}
    if display:
        display_date = _parse_iso_date(display.get("date"))
        if cutoff is None or display_date <= cutoff:
            path = _resolve_path(cfg, display.get("path"))
            if not path.is_file():
                _error("DISPLAY_SOURCE_MISSING", path)
            records = _read_workbook(path, display.get("sheet"), "编号")
            seen = set()
            for record in records:
                sku = str(record.get("编号") or "").strip()
                if not sku:
                    continue
                if not re.fullmatch(r"[0-9]+", sku) or sku in seen:
                    _error("DISPLAY_SKU_INVALID", sku)
                seen.add(sku)
                if sku not in latest or last_observed[sku] > display_date:
                    continue
                for key, column in (("name_zh", "中文品名"), ("image_url", "图片链接"), ("product_url", "商品链接")):
                    value = record.get(column)
                    if value not in (None, ""):
                        latest[sku][key] = value
    dates = tuple(sorted(catalogs))
    union = set().union(*catalogs.values())
    presence = {sku: {date: int(sku in catalogs[date]) for date in dates} for sku in union}
    return PresenceHistory(dates, presence, latest, {}, tuple(stats), None, 0,
                           strict_weekly=True, excluded_skus=excluded)
