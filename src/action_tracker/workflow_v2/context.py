from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .contracts import WorkflowContext


MADRID = ZoneInfo("Europe/Madrid")


def madrid_now() -> datetime:
    return datetime.now(MADRID)


def new_context(root: Path, *, business_date: str | None = None, run_id: str | None = None) -> WorkflowContext:
    now = madrid_now()
    date_value = business_date or now.date().isoformat()
    identifier = run_id or f"{date_value}_{now.strftime('%H%M%S')}_{uuid.uuid4().hex[:6]}"
    return WorkflowContext(workflow_run_id=identifier, business_date=date_value, started_at=now.isoformat())


def run_directory(root: Path, context: WorkflowContext) -> Path:
    path = Path(root) / context.business_date / context.workflow_run_id
    path.mkdir(parents=True, exist_ok=True)
    return path
