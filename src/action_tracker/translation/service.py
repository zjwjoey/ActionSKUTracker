"""翻译服务（规范 §32-§35）。

阶段一 translation_enabled=false：不做 AI 翻译。新 SKU / 中文缺失时：
    - 中文字段 fallback 到西语原文
    - 翻译状态 = FALLBACK_ES
    - 更新 translation_state.csv 的 source_hash / 状态
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import date
from typing import Any

from ..services.hashing import content_hash

log = logging.getLogger(__name__)

_ZH_FIELDS = ["name_zh", "spec_zh", "desc_zh", "details_zh", "cat1_zh", "cat2_zh"]
_ES_FIELDS = ["name_es", "spec_es", "desc_es", "details_es", "cat1_es", "cat2_es"]


class TranslationProvider(ABC):
    """Extension point only; the current production workflow has no network provider."""

    @abstractmethod
    def translate(self, text: str, *, source_locale: str, target_locale: str) -> str:
        raise NotImplementedError


class DisabledTranslationProvider(TranslationProvider):
    """Explicit no-network provider for the current Excel and CSV workflow."""

    def translate(self, text: str, *, source_locale: str, target_locale: str) -> str:
        return text


def apply_zh(rec: dict[str, Any]) -> dict[str, Any]:
    """确保中文字段存在；缺失则 fallback 西语，并标注翻译状态。"""
    rec = dict(rec)
    missing = [z for z, e in zip(_ZH_FIELDS, _ES_FIELDS) if not rec.get(z) and rec.get(e)]
    for z, e in zip(_ZH_FIELDS, _ES_FIELDS):
        if not rec.get(z) and rec.get(e):
            rec[z] = rec[e]
    rec["translation_status"] = "FALLBACK_ES" if missing else (rec.get("translation_status") or "NOT_CONFIGURED")
    return rec


def refresh_translation_state(trans: dict[str, dict], updated_records: dict[str, dict], state_dir) -> dict[str, dict]:
    """根据更新后的记录刷新 translation_state；西语 source_hash 变了才标记 STALE。"""
    from .. import state as st

    today = date.today().isoformat()
    for sku, rec in updated_records.items():
        cid = rec.get("canonical_id")
        if not cid:
            continue
        h = content_hash(rec)
        entry = trans.get(cid)
        if entry is None:
            trans[cid] = {
                "canonical_id": cid,
                "official_sku": sku,
                "source_hash": h,
                "translation_status": rec.get("translation_status") or "FALLBACK_ES",
                "translated_at": today,
            }
        else:
            if entry.get("source_hash") != h:
                prev = entry.get("translation_status")
                # 已有正式中文的标记 STALE（西语已变）；FALLBACK_ES 保持；STALE 保持
                new_status = "STALE" if prev == "OK" else prev
                trans[cid] = {**entry, "source_hash": h, "translation_status": new_status}
    return trans
