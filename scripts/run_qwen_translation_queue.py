"""Call Qwen-MT Flash for persisted translation queue rows.

This is the queue-side counterpart to the detail-stage integration.  It keeps
the provider call and the DB staging step in one auditable invocation, while
leaving cleaning and approval to their own workflow.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from action_tracker.config import load_settings
from action_tracker.database.integration import database_path
from action_tracker.translation.queue_runner import run_qwen_translation_queue
from action_tracker.translation.service import build_qwen_provider


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    cfg = load_settings()
    options = dict((cfg.get("translation") or {}).get("qwen_mt") or {})
    # Keep the operational endpoint override used by the local environment.
    provider = build_qwen_provider(options)
    if provider is None:
        raise SystemExit("QWEN_PROVIDER_NOT_CONFIGURED")
    report = run_qwen_translation_queue(
        database_path(cfg), provider, limit=args.limit,
        run_id=f"qwen-mt-queue-{datetime.now().strftime('%Y%m%d_%H%M%S')}",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
