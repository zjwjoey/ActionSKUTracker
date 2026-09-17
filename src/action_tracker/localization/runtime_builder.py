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
    ai_cfg = dict(localization_cfg.get("ai") or {})
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
