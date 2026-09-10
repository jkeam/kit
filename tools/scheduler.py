"""
Scheduling tools for recurring tasks.
"""

import json
import uuid
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List


def schedule_create(cron: str, task: str, description: str = "") -> str:
    """
    Create a scheduled task (cron-like).

    Args:
        cron: Cron expression (e.g., "0 9 * * *" for 9am daily)
        task: Task description/command to execute
        description: Human-readable description

    Returns:
        Success message with schedule ID
    """
    schedules_dir = Path("workspace/schedules")
    schedules_dir.mkdir(parents=True, exist_ok=True)

    schedules_file = schedules_dir / "schedules.json"

    # Load existing schedules
    if schedules_file.exists():
        schedules = json.loads(schedules_file.read_text())
    else:
        schedules = []

    # Create new schedule (uuid-based id avoids collisions after deletes)
    schedule_id = f"sched_{uuid.uuid4().hex[:8]}"
    new_schedule = {
        "id": schedule_id,
        "cron": cron,
        "task": task,
        "description": description,
        "created_at": datetime.now().isoformat(),
        "enabled": True,
        "last_run": None,
        "run_count": 0
    }

    schedules.append(new_schedule)

    # Save
    schedules_file.write_text(json.dumps(schedules, indent=2))

    return (
        f"Schedule created: {schedule_id}\n"
        f"Cron: {cron}\n"
        f"Task: {task}\n"
        f"Description: {description}"
    )


def schedule_list() -> str:
    """
    List all scheduled tasks.

    Returns:
        Formatted list of schedules
    """
    schedules_file = Path("workspace/schedules/schedules.json")

    no_schedules_message = (
        "No schedules found. Ask Kit to create a schedule (e.g. \"schedule a daily "
        "reminder at 9am\") and I'll set it up using cron syntax — daily at 9am, "
        "every 5 minutes, weekly on Mondays, etc."
    )

    if not schedules_file.exists():
        return no_schedules_message

    schedules = json.loads(schedules_file.read_text())

    if not schedules:
        return no_schedules_message

    # Format output
    lines = ["Scheduled Tasks:\n"]
    for s in schedules:
        status = "✓ Enabled" if s["enabled"] else "✗ Disabled"
        lines.append(
            f"ID: {s['id']} ({status})\n"
            f"  Cron: {s['cron']}\n"
            f"  Task: {s['task']}\n"
            f"  Description: {s.get('description', 'N/A')}\n"
            f"  Runs: {s.get('run_count', 0)}\n"
            f"  Last run: {s.get('last_run', 'Never')}\n"
        )

    return "\n".join(lines)


def schedule_delete(schedule_id: str) -> str:
    """
    Delete a scheduled task.

    Args:
        schedule_id: Schedule ID to delete

    Returns:
        Success or error message
    """
    schedules_file = Path("workspace/schedules/schedules.json")

    if not schedules_file.exists():
        return f"Error: No schedules found"

    schedules = json.loads(schedules_file.read_text())

    # Find and remove
    original_len = len(schedules)
    schedules = [s for s in schedules if s["id"] != schedule_id]

    if len(schedules) == original_len:
        return f"Error: Schedule {schedule_id} not found"

    # Save
    schedules_file.write_text(json.dumps(schedules, indent=2))

    return f"Schedule {schedule_id} deleted"


# Tool definitions
SCHEDULER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "schedule_create",
            "description": "Create a scheduled recurring task using cron syntax",
            "parameters": {
                "type": "object",
                "properties": {
                    "cron": {
                        "type": "string",
                        "description": "Cron expression (e.g., '0 9 * * *' for 9am daily, '*/5 * * * *' for every 5 min)"
                    },
                    "task": {
                        "type": "string",
                        "description": "Task to execute (command or description)"
                    },
                    "description": {
                        "type": "string",
                        "description": "Human-readable description"
                    }
                },
                "required": ["cron", "task"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_list",
            "description": "List all scheduled tasks",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_delete",
            "description": "Delete a scheduled task by ID",
            "parameters": {
                "type": "object",
                "properties": {
                    "schedule_id": {
                        "type": "string",
                        "description": "Schedule ID to delete"
                    }
                },
                "required": ["schedule_id"]
            }
        }
    }
]


# Function map
SCHEDULER_TOOL_FUNCTIONS = {
    "schedule_create": schedule_create,
    "schedule_list": schedule_list,
    "schedule_delete": schedule_delete,
}
