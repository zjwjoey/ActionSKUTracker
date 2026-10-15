"""Opt-in scheduler definitions and event identity; no task registration side effects."""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from .backup_sets import digest
from .storage_foundation import StorageError

TRIGGERS = {"manual", "daily", "at_time", "task_completed", "before_important_operation"}


@dataclass(frozen=True)
class SchedulePolicy:
    enabled: bool = False
    trigger: str = "manual"
    at_time: str = "03:00"
    retry_interval_minutes: int = 15
    retry_count: int = 2

    def __post_init__(self):
        if type(self.enabled) is not bool or self.trigger not in TRIGGERS:
            raise StorageError("SCHEDULE_CONFIG_INVALID")
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", self.at_time):
            raise StorageError("SCHEDULE_TIME_INVALID")
        if (type(self.retry_count) is not int or not 0 <= self.retry_count <= 4 or
                type(self.retry_interval_minutes) is not int or not 1 <= self.retry_interval_minutes <= 1440):
            raise StorageError("SCHEDULE_RETRY_INVALID")


def event_key(*, trigger: str, event_id: str, commit_id: str, profile: str) -> str:
    if trigger not in TRIGGERS or not event_id or not commit_id:
        raise StorageError("BACKUP_EVENT_IDENTITY_REQUIRED")
    return "backup-" + digest({"trigger": trigger, "event_id": event_id,
                                "commit_id": commit_id, "profile": profile})[:32]


def windows_task_xml(policy: SchedulePolicy, *, python_executable: Path, config_path: Path,
                     working_directory: Path, start_date: str, acceptance: dict | None = None) -> str:
    """Prepare reviewable XML only. Never registers/enables a task on Windows."""
    if policy.trigger not in {"daily", "at_time"}:
        raise StorageError("EVENT_TRIGGER_USES_EXPLICIT_CLI_HOOK")
    try:
        datetime.strptime(start_date, "%Y-%m-%d")
    except ValueError:
        raise StorageError("SCHEDULE_START_DATE_INVALID") from None
    for path in (python_executable, config_path, working_directory):
        if not Path(path).is_absolute():
            raise StorageError("SCHEDULE_ABSOLUTE_PATH_REQUIRED")
    approval = acceptance or {}
    if policy.enabled and (approval.get("local_roundtrip") != "PASS" or
            approval.get("production_enable_authorized") is not True or
            not approval.get("approved_by") or not approval.get("approval_id") or
            approval.get("config_path") != str(Path(config_path).resolve())):
        raise StorageError("SCHEDULE_PRODUCTION_ACCEPTANCE_REQUIRED")
    args = subprocess.list2cmdline(["-m", "action_tracker.operations.backup_cli", "--config",
                                   str(config_path), "scheduled"])
    trigger = (f"<CalendarTrigger><StartBoundary>{start_date}T{policy.at_time}:00</StartBoundary>"
               "<Enabled>true</Enabled><ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger>"
               if policy.trigger == "daily" else
               f"<TimeTrigger><StartBoundary>{start_date}T{policy.at_time}:00</StartBoundary><Enabled>true</Enabled></TimeTrigger>")
    retry = (f"<RestartOnFailure><Interval>PT{policy.retry_interval_minutes}M</Interval>"
             f"<Count>{policy.retry_count}</Count></RestartOnFailure>" if policy.retry_count else "")
    return ("<?xml version=\"1.0\" encoding=\"UTF-16\"?>\n"
            '<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">'
            '<RegistrationInfo><Description>Action Storage Backup; separate production activation required.</Description></RegistrationInfo>'
            f"<Triggers>{trigger}</Triggers><Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>"
            f"<Enabled>{str(policy.enabled).lower()}</Enabled><ExecutionTimeLimit>PT1H</ExecutionTimeLimit>{retry}</Settings>"
            f"<Actions><Exec><Command>{escape(str(python_executable))}</Command><Arguments>{escape(args)}</Arguments>"
            f"<WorkingDirectory>{escape(str(working_directory))}</WorkingDirectory></Exec></Actions></Task>")
