"""从现有 Master 建立初始基线状态文件（规范 §61 第四~七步）。

读取 runtime/master/Action_Master.xlsx 的副本，生成：
    known_skus.csv / sku_identity_map.csv / translation_state.csv / image_map.csv / offline_skus.csv
Master 只读，绝不修改。
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

from .excel.reader import load_current
from .services.hashing import content_hash
from . import state as st

log = logging.getLogger(__name__)


def _today() -> str:
    return date.today().isoformat()


def build_baseline(cfg: dict[str, Any], master_path: Path | None = None, force: bool = False) -> dict:
    state_dir: Path = cfg["paths"]["state"]
    master_path = master_path or cfg["paths"]["master"]
    if not master_path.exists():
        raise FileNotFoundError(f"runtime Master 不存在: {master_path}")

    records = load_current(master_path)
    log.info("Master CURRENT 读取: %d 个 SKU", len(records))

    # known_skus
    known_path = state_dir / "known_skus.csv"
    known = st.load_known_skus(state_dir) if known_path.exists() and not force else {}
    for sku, rec in records.items():
        if sku not in known:
            known[sku] = {
                "canonical_id": rec.get("canonical_id"),
                "official_sku": sku,
                "first_seen_date": rec.get("first_seen") or _today(),
                "last_seen_date": rec.get("last_seen") or _today(),
                "last_status": rec.get("status") or "CURRENT",
                "missing_count": "0",
                "ever_offline": "false",
            }
        else:
            # 已有记录：仅推进 last_seen，不改 first_seen
            ls = rec.get("last_seen") or _today()
            known[sku]["last_seen_date"] = ls
    st.save_known_skus(state_dir, known)

    # sku_identity_map
    identity = st.load_identity_map(state_dir)
    for sku, rec in records.items():
        identity.setdefault(
            sku,
            {
                "canonical_id": rec.get("canonical_id"),
                "official_sku": sku,
                "product_url": rec.get("product_url") or "",
            },
        )
    st.save_identity_map(state_dir, identity)

    # translation_state
    trans = st.load_translation_state(state_dir) if not force else {}
    for sku, rec in records.items():
        cid = rec.get("canonical_id")
        if cid in trans:
            continue
        has_zh = bool(rec.get("name_zh"))
        trans[cid] = {
            "canonical_id": cid,
            "official_sku": sku,
            "source_hash": content_hash(rec),
            "translation_status": (rec.get("translation_status") or "OK") if has_zh else "FALLBACK_ES",
            "translated_at": rec.get("last_seen") or _today(),
        }
    st.save_translation_state(state_dir, trans)

    # image_map
    imap = st.load_image_map(state_dir) if not force else {}
    for sku, rec in records.items():
        cid = rec.get("canonical_id")
        if cid in imap:
            continue
        imap[cid] = {
            "canonical_id": cid,
            "official_sku": sku,
            "local_image_path": "",
            "source_image_url": rec.get("image_url") or "",
            "download_date": rec.get("last_seen") or _today(),
            "image_hash": "",
        }
    st.save_image_map(state_dir, imap)

    # offline_skus（初始为空，建立表头）
    offline = st.load_offline_skus(state_dir)
    st.save_offline_skus(state_dir, offline)

    return {
        "baseline_skus": len(records),
        "known_skus": len(known),
        "identity_map": len(identity),
        "translation_state": len(trans),
        "image_map": len(imap),
        "offline": len(offline),
    }
