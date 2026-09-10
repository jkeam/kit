"""
Scheduler - background cron runner that sends due tasks to the assistant.

Checks schedules every 60 seconds. Each scheduled task runs in an isolated
session (scheduler:{schedule_id}) so it doesn't pollute interactive chat.
Scheduled sessions cannot create new schedules (anti-recursion).
"""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

SCHEDULES_FILE = Path("workspace/schedules/schedules.json")


def cron_matches(expression: str, dt: datetime) -> bool:
    """
    Check if a 5-field cron expression matches a datetime.

    Fields: minute hour day-of-month month day-of-week
    Supports: exact values, '*', '*/N' (step), comma-separated lists.
    Day-of-week: 0=Sunday through 6=Saturday (7 also accepted as Sunday).
    """
    fields = expression.strip().split()
    if len(fields) != 5:
        return False

    values = [dt.minute, dt.hour, dt.day, dt.month, dt.isoweekday() % 7]
    ranges = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]

    for field, value, (lo, hi) in zip(fields, values, ranges):
        if not _field_matches(field, value, lo, hi):
            return False
    return True


def _field_matches(field: str, value: int, lo: int, hi: int) -> bool:
    for part in field.split(","):
        if part == "*":
            return True
        if part.startswith("*/"):
            try:
                step = int(part[2:])
                if step > 0 and (value - lo) % step == 0:
                    return True
            except ValueError:
                continue
        else:
            try:
                if int(part) == value:
                    return True
            except ValueError:
                continue
    return False


def _load_schedules():
    if not SCHEDULES_FILE.exists():
        return []
    try:
        return json.loads(SCHEDULES_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def _save_schedules(schedules):
    SCHEDULES_FILE.parent.mkdir(parents=True, exist_ok=True)
    SCHEDULES_FILE.write_text(json.dumps(schedules, indent=2))


async def run_scheduler(session_manager, ws_manager) -> None:
    """Background loop — call once from gateway startup."""
    while True:
        await asyncio.sleep(60)
        try:
            await _tick(session_manager, ws_manager)
        except Exception as e:
            print(f"Scheduler error: {e}")


async def _tick(session_manager, ws_manager) -> None:
    now = datetime.now()
    schedules = _load_schedules()
    changed = False

    for sched in schedules:
        if not sched.get("enabled", True):
            continue
        if not cron_matches(sched["cron"], now):
            continue

        last_run = sched.get("last_run")
        if last_run:
            last_dt = datetime.fromisoformat(last_run)
            if (now - last_dt).total_seconds() < 90:
                continue

        schedule_id = sched["id"]
        task = sched["task"]
        print(f"Scheduler: running {schedule_id} — {task}")

        try:
            response = await session_manager.send_message("scheduler", schedule_id, task)

            await ws_manager.broadcast({
                "type": "schedule_run",
                "schedule_id": schedule_id,
                "task": task,
                "result": response[:500],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as e:
            response = f"Error: {e}"
            print(f"Scheduler: {schedule_id} failed — {e}")

        sched["last_run"] = now.isoformat()
        sched["run_count"] = sched.get("run_count", 0) + 1
        changed = True

    if changed:
        _save_schedules(schedules)
