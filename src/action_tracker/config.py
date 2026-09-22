"""加载 config/settings.yaml。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def project_root() -> Path:
    return _PROJECT_ROOT


def load_settings(path: Path | str | None = None) -> dict[str, Any]:
    p = Path(path) if path else _PROJECT_ROOT / "config" / "settings.yaml"
    if not p.exists():
        raise FileNotFoundError(f"配置文件不存在: {p}")
    with p.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    # 解析绝对路径（相对路径基于项目根）
    for key in ("master", "snapshots", "staging", "state", "dictionary", "dictionary_baseline", "review_queue", "images", "exports", "history_archive", "logs", "backups", "temp", "category_primary_map"):
        if key in cfg.get("paths", {}):
            raw = cfg["paths"][key]
            pth = Path(raw)
            if not pth.is_absolute():
                pth = _PROJECT_ROOT / pth
            cfg["paths"][key] = pth
    for browser_key in ("profile_dir", "cookies_path"):
        if browser_key not in cfg.get("browser", {}) or not cfg["browser"][browser_key]:
            continue
        value = Path(cfg["browser"][browser_key])
        if not value.is_absolute():
            value = _PROJECT_ROOT / value
        cfg["browser"][browser_key] = value
    cfg["project_root"] = _PROJECT_ROOT
    return cfg


def ensure_runtime_dirs(cfg: dict[str, Any]) -> None:
    """确保所有 runtime 目录存在（master 是文件路径，跳过）。"""
    for key, p in cfg["paths"].items():
        if key in {"master", "category_primary_map"}:
            continue
        if isinstance(p, Path):
            p.mkdir(parents=True, exist_ok=True)
