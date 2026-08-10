"""Git 提交信息获取（规范：05_RUN_LOG 记录每次运行的代码版本）。

捕获 `git rev-parse --short HEAD`；工作区有未提交改动时追加 `-dirty`。
获取失败返回 "unknown"（不阻断运行）。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]  # src/action_tracker/services -> 项目根


def _git(args: list[str]) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()


def git_commit_info(repo_dir: Path | None = None) -> str:
    try:
        root = str(repo_dir or _PROJECT_ROOT)
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root, capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        if not sha:
            return "unknown"
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root, capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        return f"{sha}-dirty" if dirty else sha
    except Exception:
        return "unknown"
