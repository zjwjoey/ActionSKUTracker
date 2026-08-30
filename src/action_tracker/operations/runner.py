"""Resumable, deterministic production step runner.

Business steps are injected so tests can fault-inject without network or a
production database. The real daily collector remains the existing service.
"""
from __future__ import annotations

import json
import os
import socket
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping

from ..services.runtime import RunLock, madrid_now
from .contracts import EXIT_CODES, NON_BLOCKING_STEPS, STEP_DEPENDENCIES, STEP_ORDER, RunState, StepResult
from .reporter import write_daily_report


class ProductionRunner:
    def __init__(self, *, root: Path, business_date: str, run_id: str | None = None,
                 steps: Mapping[str, Callable[[], StepResult | Mapping | None]], lock_dir: Path | None = None):
        self.root = Path(root)
        self.business_date = business_date
        self.run_id = run_id or f"{business_date}_{madrid_now().strftime('%H%M%S')}_{uuid.uuid4().hex[:6]}"
        self.steps = dict(steps)
        self.run_dir = self.root / business_date / self.run_id
        self.state_path = self.run_dir / "state.json"
        self.lock = RunLock(Path(lock_dir or self.root.parent / "locks"), stale_minutes=180)

    def run(self, *, resume: bool = False, from_step: str | None = None) -> dict:
        if from_step and from_step not in STEP_ORDER:
            raise ValueError("UNKNOWN_FROM_STEP")
        self.run_dir.mkdir(parents=True, exist_ok=True)
        state = self._load_state() if resume else self._new_state()
        if resume and state.get("state") == RunState.SUCCESS.value:
            return state
        self.lock.acquire(self.run_id, command="production-run")
        errors = list(state.get("errors", [])); started = state.get("started_at") or datetime.now().isoformat()
        try:
            start_index = STEP_ORDER.index(from_step) if from_step else 0
            self._check_dependencies(state, start_index, resume)
            for index, step in enumerate(STEP_ORDER):
                if index < start_index: continue
                previous = state["steps"].get(step)
                if resume and previous and previous.get("status") == "SUCCESS": continue
                state["state"] = RunState.PREFLIGHT.value if step == "PREFLIGHT" else RunState.RUNNING.value
                self.lock.heartbeat()
                state["steps"][step] = {"status": "RUNNING", "started_at": datetime.now().isoformat()}
                self._save(state)
                try:
                    raw = self.steps.get(step, lambda: StepResult("SKIPPED"))()
                    result = raw if isinstance(raw, StepResult) else StepResult("SUCCESS", dict(raw or {}))
                except Exception as exc:
                    result = StepResult("FAILED", {}, False, type(exc).__name__)
                    errors.append({"step": step, "code": result.error_code, "message": str(exc), "retryable": result.retryable})
                state["steps"][step].update({"status": result.status, "details": result.details, "retryable": result.retryable, "error_code": result.error_code, "finished_at": datetime.now().isoformat()})
                if result.status in {"FAILED", "BLOCKED"}:
                    if step in NON_BLOCKING_STEPS and "DB_COMMIT" in state["steps"] and state["steps"]["DB_COMMIT"].get("status") == "SUCCESS":
                        state["state"] = RunState.DEGRADED.value
                    else:
                        state["state"] = RunState.BLOCKED.value if result.status == "BLOCKED" else RunState.FAILED.value
                        self._save(state); break
                self._save(state)
            if state["state"] not in {RunState.FAILED.value, RunState.BLOCKED.value}:
                failed = [s for s in state["steps"].values() if s.get("status") in {"FAILED", "BLOCKED"}]
                state["state"] = RunState.DEGRADED.value if failed else RunState.SUCCESS.value
            state["errors"] = errors; state["finished_at"] = datetime.now().isoformat(); state["exit_code"] = EXIT_CODES[state["state"]]
            self._save(state)
            write_daily_report(self.root, self.business_date, self.run_id, {k: state[k] for k in ("run_id", "business_date", "state", "started_at", "finished_at", "exit_code")}, [{"step": k, **v} for k, v in state["steps"].items()], errors)
            return state
        finally:
            self.lock.release()

    def _new_state(self) -> dict:
        return {"run_id": self.run_id, "business_date": self.business_date, "state": RunState.CREATED.value, "started_at": None, "finished_at": None, "exit_code": None, "steps": {}, "errors": [], "pid": os.getpid(), "hostname": socket.gethostname()}

    def _load_state(self) -> dict:
        if not self.state_path.exists(): raise FileNotFoundError("RUN_STATE_MISSING")
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def _save(self, state: dict) -> None:
        state["started_at"] = state.get("started_at") or datetime.now().isoformat()
        tmp = self.state_path.with_suffix(".tmp"); tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"); tmp.replace(self.state_path)

    def _check_dependencies(self, state: dict, start_index: int, resume: bool) -> None:
        if start_index == 0: return
        for step in STEP_ORDER[:start_index]:
            if state.get("steps", {}).get(step, {}).get("status") != "SUCCESS":
                raise ValueError(f"FROM_STEP_DEPENDENCY_NOT_SATISFIED:{step}")
