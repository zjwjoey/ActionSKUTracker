from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_daily_report(root: Path, business_date: str, run_id: str, summary: dict[str, Any], steps: list[dict[str, Any]], errors: list[dict[str, Any]]) -> Path:
    directory = Path(root) / business_date / run_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    (directory / "steps.json").write_text(json.dumps(steps, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    (directory / "errors.json").write_text(json.dumps(errors, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    lines = [f"# Production Run {run_id}", "", f"- Business date: {business_date}", f"- State: {summary.get('state')}", f"- Exit code: {summary.get('exit_code')}", "", "## Steps", ""]
    lines.extend(f"- {item.get('step')}: {item.get('status')}" for item in steps)
    if errors:
        lines += ["", "## Errors", ""]
        lines.extend(f"- {item.get('step')}: {item.get('code')} — {item.get('message')}" for item in errors)
    (directory / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return directory
