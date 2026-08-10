"""文件型状态数据读写（规范 §12-§13、§35-§36）。

维护 runtime/state 下的机器状态文件：
    known_skus.csv        系统历史认识的全部商品身份
    offline_skus.csv      OFFLINE 商品
    sku_identity_map.csv  canonical_id <-> SKU <-> URL
    translation_state.csv 中文翻译状态
    image_map.csv         本地图片映射
"""
from __future__ import annotations

import csv
from pathlib import Path

KNOWN_HEADERS = ["canonical_id", "official_sku", "first_seen_date", "last_seen_date", "last_status", "missing_count", "ever_offline"]
OFFLINE_HEADERS = ["canonical_id", "official_sku", "offline_date", "last_seen_date", "last_status"]
IDENTITY_HEADERS = ["canonical_id", "official_sku", "product_url"]
TRANS_HEADERS = ["canonical_id", "official_sku", "source_hash", "translation_status", "translated_at"]
IMAGE_HEADERS = ["canonical_id", "official_sku", "local_image_path", "source_image_url", "download_date", "image_hash"]


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _write_csv(path: Path, rows: list[dict], headers: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


# ---- known_skus ----

def load_known_skus(state_dir: Path) -> dict[str, dict]:
    rows = _read_csv(state_dir / "known_skus.csv")
    out = {}
    for r in rows:
        sku = (r.get("official_sku") or "").strip()
        if sku:
            r.setdefault("missing_count", "0")
            r.setdefault("ever_offline", "false")
            out[sku] = r
    return out


def save_known_skus(state_dir: Path, known: dict[str, dict]) -> None:
    rows = sorted(known.values(), key=lambda r: r.get("official_sku", ""))
    _write_csv(state_dir / "known_skus.csv", rows, KNOWN_HEADERS)


# ---- offline_skus ----

def load_offline_skus(state_dir: Path) -> dict[str, dict]:
    rows = _read_csv(state_dir / "offline_skus.csv")
    return {r.get("official_sku"): r for r in rows if r.get("official_sku")}


def save_offline_skus(state_dir: Path, offline: dict[str, dict]) -> None:
    _write_csv(state_dir / "offline_skus.csv", sorted(offline.values(), key=lambda r: r.get("official_sku", "")), OFFLINE_HEADERS)


# ---- sku_identity_map ----

def load_identity_map(state_dir: Path) -> dict[str, dict]:
    rows = _read_csv(state_dir / "sku_identity_map.csv")
    return {r.get("official_sku"): r for r in rows if r.get("official_sku")}


def save_identity_map(state_dir: Path, mapping: dict[str, dict]) -> None:
    _write_csv(state_dir / "sku_identity_map.csv", sorted(mapping.values(), key=lambda r: r.get("official_sku", "")), IDENTITY_HEADERS)


# ---- translation_state ----

def load_translation_state(state_dir: Path) -> dict[str, dict]:
    rows = _read_csv(state_dir / "translation_state.csv")
    return {r.get("canonical_id"): r for r in rows if r.get("canonical_id")}


def save_translation_state(state_dir: Path, trans: dict[str, dict]) -> None:
    _write_csv(state_dir / "translation_state.csv", sorted(trans.values(), key=lambda r: r.get("canonical_id", "")), TRANS_HEADERS)


# ---- image_map ----

def load_image_map(state_dir: Path) -> dict[str, dict]:
    rows = _read_csv(state_dir / "image_map.csv")
    return {r.get("canonical_id"): r for r in rows if r.get("canonical_id")}


def save_image_map(state_dir: Path, imap: dict[str, dict]) -> None:
    _write_csv(state_dir / "image_map.csv", sorted(imap.values(), key=lambda r: r.get("canonical_id", "")), IMAGE_HEADERS)
