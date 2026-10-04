"""加载 config/settings.yaml。"""
from __future__ import annotations

import os
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

import yaml

# The checked-out source tree and the production data root may be different
# (for example, a validated worktree can operate on F:\\ActionSKUTracker's
# runtime).  Keep the source-tree default for development, while allowing the
# scheduler wrapper to select the explicit production data root.
_PROJECT_ROOT = Path(os.environ.get("ACTION_TRACKER_PROJECT_ROOT") or Path(__file__).resolve().parent.parent.parent).resolve()


def project_root() -> Path:
    return _PROJECT_ROOT


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Merge a partial runtime overlay without dropping base subtrees."""
    result = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(dict(result[key]), value)
        else:
            result[key] = value
    return result


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items() if key != "_config_evidence"}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def config_evidence(cfg: dict[str, Any]) -> dict[str, Any]:
    """Return non-secret evidence describing the effective runtime config."""
    return dict(cfg.get("_config_evidence") or {})


def validate_phase1_profile(cfg: dict[str, Any]) -> list[str]:
    """Return the controlled Phase 1 production-translation gate failures."""
    workflow = dict(cfg.get("workflow_v2") or {})
    auto_translation = dict(workflow.get("auto_translation") or {})
    auto_policy = dict(workflow.get("auto_policy_approval") or {})
    auto_export = dict(workflow.get("auto_export") or {})
    localization = dict(cfg.get("localization") or {})
    ai = dict(localization.get("ai") or {})
    knowledge = dict(cfg.get("knowledge") or {})
    expected = {
        "workflow_v2.enabled": (workflow.get("enabled") is True),
        "workflow_v2.auto_translation.enabled": (auto_translation.get("enabled") is True),
        "workflow_v2.auto_translation.provider": str(auto_translation.get("provider") or "") == "qwen_mt",
        "workflow_v2.auto_policy_approval.enabled": (auto_policy.get("enabled") is False),
        "workflow_v2.auto_export.enabled": (auto_export.get("enabled") is False),
        "localization.ai.enabled": (ai.get("enabled") is True),
        "localization.ai.provider": str(ai.get("provider") or "") == "qwen_mt",
        "localization.production_apply_enabled": (localization.get("production_apply_enabled") is False),
        "knowledge.production_apply_enabled": (knowledge.get("production_apply_enabled") is False),
        "knowledge.fallback_to_spanish": (knowledge.get("fallback_to_spanish") is False),
    }
    return [name for name, valid in expected.items() if not valid]


def load_settings(path: Path | str | None = None, *, overlay_paths: Iterable[Path | str] = ()) -> dict[str, Any]:
    """Load base settings and apply one or more explicit partial overlays."""
    base_path = (Path(path) if path else _PROJECT_ROOT / "config" / "settings.yaml").resolve()
    if not base_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {base_path}")
    with base_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    profile_paths: list[Path] = []
    for raw_overlay in overlay_paths:
        overlay_path = Path(raw_overlay)
        if not overlay_path.is_absolute():
            overlay_path = _PROJECT_ROOT / overlay_path
        overlay_path = overlay_path.resolve()
        if not overlay_path.exists():
            raise FileNotFoundError(f"配置 overlay 不存在: {overlay_path}")
        with overlay_path.open("r", encoding="utf-8") as f:
            overlay = yaml.safe_load(f) or {}
        if not isinstance(overlay, dict):
            raise ValueError(f"配置 overlay 必须是 mapping: {overlay_path}")
        cfg = _deep_merge(cfg, overlay)
        profile_paths.append(overlay_path)
    # 解析绝对路径（相对路径基于项目根）
    for key in ("master", "snapshots", "staging", "state", "dictionary", "dictionary_baseline", "review_queue", "images", "exports", "logs", "backups", "temp"):
        if key in cfg.get("paths", {}):
            raw = cfg["paths"][key]
            pth = Path(raw)
            if not pth.is_absolute():
                pth = _PROJECT_ROOT / pth
            cfg["paths"][key] = pth
    if "profile_dir" in cfg.get("browser", {}):
        profile = Path(cfg["browser"]["profile_dir"])
        if not profile.is_absolute():
            profile = _PROJECT_ROOT / profile
        cfg["browser"]["profile_dir"] = profile
    _apply_fail_closed_defaults(cfg)
    cfg["project_root"] = _PROJECT_ROOT
    canonical = json.dumps(_json_safe(cfg), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    cfg["_config_evidence"] = {
        "base_config_path": str(base_path),
        "profile_path": str(profile_paths[-1]) if profile_paths else None,
        "base_config_sha256": _sha256_file(base_path),
        "profile_sha256": _sha256_file(profile_paths[-1]) if profile_paths else None,
        "effective_config_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }
    return cfg


def _apply_fail_closed_defaults(cfg: dict[str, Any]) -> None:
    """Normalize Translation V1 safety switches without ever opening them.

    Configuration files are allowed to be older or minimal fixtures.  Missing
    production, auto-approval, AI, or Spanish-fallback switches must resolve
    to the safe state rather than relying on every caller to remember a
    default.  Explicit values are preserved for operator-controlled previews.
    """
    knowledge = dict(cfg.get("knowledge") or {})
    knowledge.setdefault("production_apply_enabled", False)
    knowledge.setdefault("fallback_to_spanish", False)
    cfg["knowledge"] = knowledge

    localization = dict(cfg.get("localization") or {})
    localization.setdefault("production_apply_enabled", False)
    localization.setdefault("auto_approval_enabled", False)
    ai = dict(localization.get("ai") or {})
    ai.setdefault("enabled", False)
    localization["ai"] = ai
    cfg["localization"] = localization

    translation = dict(cfg.get("translation") or {})
    translation.setdefault("ai_enabled", False)
    translation.setdefault("auto_approval_enabled", False)
    cfg["translation"] = translation


def ensure_runtime_dirs(cfg: dict[str, Any]) -> None:
    """确保所有 runtime 目录存在（master 是文件路径，跳过）。"""
    for key, p in cfg["paths"].items():
        if key == "master":
            continue
        if isinstance(p, Path):
            p.mkdir(parents=True, exist_ok=True)
