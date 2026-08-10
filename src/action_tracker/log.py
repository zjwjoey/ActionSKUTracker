"""统一日志：控制台 + 每日日志文件。"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path


def get_logger(name: str = "action_tracker") -> logging.Logger:
    return logging.getLogger(name)


def setup_logging(log_dir: Path | None, run_date: str | None = None, verbose: bool = False) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    # 控制台
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    if log_dir is not None:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        day = run_date or datetime.now().strftime("%Y-%m-%d")
        fh = logging.FileHandler(log_dir / f"run_{day}.log", encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
