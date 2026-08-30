from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_daily_report(root: Path, business_date: str, run_id: str, summary: dict[str, Any], steps: list[dict[str, Any]], errors: list[dict[str, Any]]) -> Path:
    directory = Path(root) / business_date / run_id
    directory.mkdir(parents=True, exist_ok=True)
    payloads = {
        "summary.json": json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
        "steps.json": json.dumps(steps, ensure_ascii=False, indent=2, default=str) + "\n",
        "errors.json": json.dumps(errors, ensure_ascii=False, indent=2, default=str) + "\n",
    }
    for name, payload in payloads.items():
        target = directory / name
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(target)
    lines = [f"# Production Run {run_id}", "", f"- Business date: {business_date}", f"- State: {summary.get('state')}", f"- Exit code: {summary.get('exit_code')}", "", "## Steps", ""]
    lines.extend(f"- {item.get('step')}: {item.get('status')}" for item in steps)
    if errors:
        lines += ["", "## Errors", ""]
        lines.extend(f"- {item.get('step')}: {item.get('code')} — {item.get('message')}" for item in errors)
    markdown = directory / "summary.md"
    temporary = markdown.with_suffix(markdown.suffix + ".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(markdown)
    return directory
