"""翻译服务（规范 §32-§35）。

详情阶段可按配置调用本地 Qwen-MT Flash；该模块只负责原文到中文的
投影和调用诊断，不做后续清洗或审核。调用失败时保留既有西语 fallback。
"""
from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from datetime import date
from typing import Any

from ..services.hashing import content_hash
from .qwen_mt import QwenMTFlashProvider

log = logging.getLogger(__name__)

_ZH_FIELDS = ["name_zh", "spec_zh", "desc_zh", "details_zh", "cat1_zh", "cat2_zh"]
_ES_FIELDS = ["name_es", "spec_es", "desc_es", "details_es", "cat1_es", "cat2_es"]
_QWEN_RETRY_STATUSES = {"", "FALLBACK_ES", "NOT_CONFIGURED", "PENDING", "STALE", "QWEN_FAILED", "QWEN_PARTIAL"}


class TranslationProvider(ABC):
    """Extension point only; the current production workflow has no network provider."""

    @abstractmethod
    def translate(self, text: str, *, source_locale: str, target_locale: str) -> str:
        raise NotImplementedError


class DisabledTranslationProvider(TranslationProvider):
    """Explicit no-network provider for the current Excel and CSV workflow."""

    def translate(self, text: str, *, source_locale: str, target_locale: str) -> str:
        return text


def build_qwen_provider(config: dict[str, Any] | None = None) -> QwenMTFlashProvider | None:
    """Build the detail-stage Qwen provider from config and environment.

    A missing key is handled as a disabled runtime capability.  The daily
    extraction can therefore still produce its existing Spanish fallback and
    record the reason instead of failing the collection itself.
    """
    options = dict(config or {})
    if not bool(options.get("enabled", False)):
        return None
    key_env = str(options.get("api_key_env") or "DASHSCOPE_API_KEY")
    if not str(options.get("api_key") or os.environ.get(key_env, "")).strip():
        return None
    return QwenMTFlashProvider(
        endpoint=options.get("endpoint") or options.get("base_url"),
        api_key=options.get("api_key"),
        api_key_env=key_env,
        model=str(options.get("model") or "qwen-mt-flash"),
        timeout=float(options.get("timeout", 60)),
        max_retries=int(options.get("max_retries", 3)),
        backoff_seconds=float(options.get("backoff_seconds", 5)),
        rate_limit_per_second=float(options.get("rate_limit_per_second", 0.5)),
    )


def translate_records_with_qwen(
    records: dict[str, dict[str, Any]],
    provider: QwenMTFlashProvider | Any,
    *,
    eligible_skus: set[str] | None = None,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Translate raw fields for the SKUs whose detail extraction just ended.

    This is intentionally a field projection only: it does not clean, repair,
    normalize, or approve model output.  A provider failure leaves the field
    available for the existing fallback path and is exposed in the run report.
    """
    selected = set(eligible_skus or records)
    updates: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "provider": "qwen-mt-flash",
        "enabled": True,
        "eligible_skus": len(selected),
        "translated_skus": 0,
        "field_calls": 0,
        "field_successes": 0,
        "field_failures": 0,
        "failed_fields": [],
    }
    for sku, original in records.items():
        if sku not in selected:
            continue
        rec = dict(original)
        status = str(rec.get("translation_status") or "").strip().upper()
        changed_fields: list[str] = []
        failed_fields: list[str] = []
        for zh_field, es_field in zip(_ZH_FIELDS, _ES_FIELDS):
            source = str(rec.get(es_field) or "").strip()
            if not source:
                continue
            current = str(rec.get(zh_field) or "").strip()
            # Preserve an existing non-fallback translation.  New/changed
            # records and legacy Spanish fallbacks are sent to Qwen.
            if current and current != source and status not in _QWEN_RETRY_STATUSES:
                continue
            report["field_calls"] += 1
            try:
                result = provider.translate(source, source_locale="es", target_locale="zh-CN", field=es_field, sku=sku)
                text = str(getattr(result, "text", result) or "").strip()
                error = getattr(result, "error", None)
            except Exception as exc:  # provider failures are per-field
                text, error = "", f"{type(exc).__name__}: {exc}"
            if text and not error:
                rec[zh_field] = text
                changed_fields.append(zh_field)
                report["field_successes"] += 1
            else:
                failed_fields.append(zh_field)
                report["field_failures"] += 1
                report["failed_fields"].append({"sku": sku, "field": zh_field, "error": error or "EMPTY_TRANSLATION"})
        if changed_fields:
            rec["translation_status"] = "QWEN_TRANSLATED" if not failed_fields else "QWEN_PARTIAL"
            report["translated_skus"] += 1
            updates.append({
                "sku": sku,
                "canonical_id": rec.get("canonical_id"),
                "translation_status": rec["translation_status"],
                "fields": changed_fields,
                "failed_fields": failed_fields,
            })
        elif failed_fields:
            rec["translation_status"] = "QWEN_FAILED"
            updates.append({
                "sku": sku,
                "canonical_id": rec.get("canonical_id"),
                "translation_status": "QWEN_FAILED",
                "fields": [],
                "failed_fields": failed_fields,
            })
        records[sku] = rec
    return records, updates, report


def apply_zh(rec: dict[str, Any]) -> dict[str, Any]:
    """Apply the legacy projection without violating the source-empty contract.

    Non-empty fields retain the historical same-field fallback marker for
    compatibility.  An empty official field is explicitly cleared and never
    filled from another field; formal export paths should prefer the gated
    dictionary/database resolver instead of this compatibility helper.
    """
    rec = dict(rec)
    missing = [z for z, e in zip(_ZH_FIELDS, _ES_FIELDS) if not rec.get(z) and rec.get(e)]
    for z, e in zip(_ZH_FIELDS, _ES_FIELDS):
        if not rec.get(e):
            rec[z] = None
        elif not rec.get(z):
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
