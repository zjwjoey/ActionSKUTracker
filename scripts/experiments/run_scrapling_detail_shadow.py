"""Standalone entry point for the Scrapling detail parser shadow experiment."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from action_tracker.experiments.scrapling_detail.runner import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
