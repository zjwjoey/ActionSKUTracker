"""Single Translation System V1 runtime construction boundary."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Mapping

from ..database.connection import connect
from ..database.integration import database_path
from ..config import load_settings
from .ai import DisabledProvider, provider_from_config
from .engine import LocalizationEngine
from .knowledge import KnowledgeLoader
from .registry.repository import LocalizationRegistry
from .resolver import TranslationResolver
from .worker import TranslationQueueWorker


def _database_role(path: Path) -> str:
    if not path.exists():
        return "SHADOW"
    try:
        with connect(path) as db:
            row = db.execute("SELECT value FROM schema_metadata WHERE key='database_role'").fetchone()
        return str(row[0]) if row and str(row[0]) in {"PRIMARY", "SHADOW"} else "SHADOW"
    except Exception:
        return "SHADOW"


@dataclass
class TranslationRuntime:
    cfg: Mapping[str, Any]
    db_path: Path
    registry: LocalizationRegistry
    resolver: TranslationResolver
    provider: Any
    worker: TranslationQueueWorker


def effective_ai_config(cfg: Mapping[str, Any]) -> dict[str, Any]:
    """Return the V1 AI profile, bridging older production settings safely.

    Older data roots enabled the dedicated adapter under
    ``translation.qwen_mt`` before the V1 ``localization.ai`` profile was
    introduced.  Only a missing V1 profile is bridged; an explicit V1 profile
    (including ``enabled: false``) remains authoritative and fail-closed.
    """
    localization_cfg = dict(cfg.get("localization") or {})
    ai_cfg = dict(localization_cfg.get("ai") or {})
    # ``load_settings`` adds ``enabled: false`` to missing profiles for
    # fail-closed defaults.  Treat that defaults-only shape as missing; a
    # profile with provider/model/endpoint metadata is an explicit operator
    # choice and remains authoritative.
    has_explicit_profile = bool(ai_cfg.get("enabled")) or any(
        str(ai_cfg.get(key) or "").strip() for key in ("provider", "base_url", "model", "api_key_env")
    )
    if has_explicit_profile:
        return ai_cfg
    legacy = dict((cfg.get("translation") or {}).get("qwen_mt") or {})
    if not bool(legacy.get("enabled")):
        return ai_cfg
    return {
        "enabled": True,
        "provider": "qwen_mt",
        "base_url": legacy.get("endpoint") or legacy.get("base_url") or os.environ.get("QWEN_MT_BASE_URL") or os.environ.get("DASHSCOPE_BASE_URL"),
        "model": legacy.get("model") or "qwen-mt-flash",
        "api_key_env": legacy.get("api_key_env") or "DASHSCOPE_API_KEY",
        "timeout": legacy.get("timeout") or 60,
        "max_retries": legacy.get("max_retries") if legacy.get("max_retries") is not None else 2,
        "backoff_seconds": legacy.get("backoff_seconds") if legacy.get("backoff_seconds") is not None else 5.0,
        "rate_limit_per_second": legacy.get("rate_limit_per_second") if legacy.get("rate_limit_per_second") is not None else 0.5,
    }


def build_translation_runtime(cfg: Mapping[str, Any] | None = None, *, db_path: Path | None = None,
                              allow_provider: bool = False) -> TranslationRuntime:
    """Build Registry, knowledge, engine, provider, resolver and worker once.

    Shadow/Canary/Worker all use this boundary so configuration cannot drift.
    ``allow_provider`` is an explicit call-site permission; the config flag
    must still be enabled before a network provider is constructed.
    """
    cfg = dict(cfg or load_settings())
    path = Path(db_path or database_path(cfg))
    role = _database_role(path)
    registry = LocalizationRegistry(path, role=role)
    dictionary_dir = Path((cfg.get("paths") or {}).get("dictionary") or Path(cfg["project_root"]) / "runtime" / "dictionary")
    knowledge = KnowledgeLoader(dictionary_dir).load() if dictionary_dir.exists() else {}
    localization_cfg = cfg.get("localization") or {}
    ai_cfg = effective_ai_config(cfg)
    # An explicit provider permission is necessary but never sufficient to
    # override the production configuration gate.
    # The repository configuration keeps AI disabled by default.  A read-only
    # Canary may explicitly opt in through the wrapper's process-scoped flag;
    # this never enables production writes and disappears with the process.
    explicit_canary_enable = os.environ.get("ACTION_TRACKER_ALLOW_QWEN_PROVIDER") == "1"
    if explicit_canary_enable and not ai_cfg.get("provider"):
        # A production data root may still carry an older settings.yaml that
        # predates the Translation V1 localization.ai block.  An explicit
        # read-only Canary must still select Qwen-MT, never the legacy generic
        # OpenAI-compatible provider (which has no translate() contract).
        ai_cfg.update({
            "provider": "qwen_mt",
            "model": os.environ.get("QWEN_MT_MODEL") or "qwen-mt-flash",
            "api_key_env": "DASHSCOPE_API_KEY",
        })
    provider = provider_from_config({
        **ai_cfg,
        "enabled": bool(allow_provider and (ai_cfg.get("enabled") or explicit_canary_enable)),
    })
    engine = LocalizationEngine(knowledge=knowledge, policy_version=str(localization_cfg.get("policy_version") or "CHINESE_LOCALIZATION_STANDARD_V1"))
    resolver = TranslationResolver(db_path=path, registry=registry,
                                   provider=None if isinstance(provider, DisabledProvider) else provider,
                                   engine=engine)
    return TranslationRuntime(cfg, path, registry, resolver, provider, TranslationQueueWorker(registry, resolver))
