"""
Dream Cycle - background reflection that consolidates memories during idle periods.

Inspired by OpenClaw's dream phase: during a configurable cron window (default
3 AM daily), the assistant reviews recent daily logs, looks for patterns and
recurring themes, and writes a dream log with consolidated insights.  Dream
logs live in workspace/memory/{agent_id}/dreams/ — separate from MEMORY.md so
hallucinated "insights" never pollute real memory without review.

Uses the same cron infrastructure and isolated-session pattern as the
scheduler (gateway/scheduler.py).
"""

import asyncio
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from gateway.scheduler import cron_matches
from env_config import env_int


DREAM_PROMPT_TEMPLATE = """\
You are entering a dream cycle — a reflective phase where you review recent \
interactions and consolidate your understanding.

Below are the daily interaction logs from the last {lookback_days} day(s).  \
Read them carefully and produce a dream report with the following sections:

## Patterns & Themes
Recurring topics, questions, or workflows you notice across interactions.

## Insights
Non-obvious connections, things learned, or realisations that emerged from \
reviewing these interactions together rather than individually.

## Unresolved Threads
Questions that were asked but not fully answered, tasks that seem incomplete, \
or topics the user might want to revisit.

## Suggested Memories
Specific facts or preferences worth remembering long-term (candidates for \
promotion to MEMORY.md).  Be selective — only include things that would \
genuinely help future interactions.

---

RECENT LOGS:

{logs}
"""


def _dream_cron() -> str:
    return os.environ.get("DREAM_CRON", "0 3 * * *")


def _dream_lookback_days() -> int:
    return env_int("DREAM_LOOKBACK_DAYS", 3)


def _dream_enabled() -> bool:
    return os.environ.get("DREAM_ENABLED", "true").lower() in ("true", "1", "yes")


def _dream_agent_id() -> str:
    return os.environ.get("DREAM_AGENT_ID", "kit")


def _dreams_dir(workspace_dir: Path, agent_id: str) -> Path:
    return workspace_dir / "memory" / agent_id / "dreams"


def _collect_recent_logs(workspace_dir: Path, agent_id: str, days: int) -> str:
    """Read the last `days` daily logs for an agent and return them concatenated."""
    memory_dir = workspace_dir / "memory" / agent_id
    parts: list[str] = []

    for i in range(days):
        date = datetime.now() - timedelta(days=i)
        log_path = memory_dir / f"{date.strftime('%Y-%m-%d')}.md"
        if log_path.exists():
            content = log_path.read_text().strip()
            if content:
                parts.append(content)

    return "\n\n---\n\n".join(parts) if parts else ""


def list_dreams(workspace_dir: Path, agent_id: str) -> list[dict]:
    """Return metadata for all dream logs, newest first."""
    dreams_path = _dreams_dir(workspace_dir, agent_id)
    if not dreams_path.exists():
        return []

    results = []
    for path in sorted(dreams_path.glob("*.md"), reverse=True):
        results.append({
            "date": path.stem,
            "file": str(path),
            "size": path.stat().st_size,
        })
    return results


def get_dream(workspace_dir: Path, agent_id: str, date: str) -> Optional[str]:
    """Read a specific dream log by date (YYYY-MM-DD)."""
    path = _dreams_dir(workspace_dir, agent_id) / f"{date}.md"
    if path.exists():
        return path.read_text()
    return None


async def run_dream_cycle(session_manager, ws_manager) -> None:
    """Background loop — call once from gateway startup alongside the scheduler."""
    while True:
        await asyncio.sleep(60)
        if not _dream_enabled():
            continue
        try:
            await _tick(session_manager, ws_manager)
        except Exception as e:
            print(f"Dreamer error: {e}")


_last_dream_run: Optional[datetime] = None


async def _tick(session_manager, ws_manager) -> None:
    global _last_dream_run

    now = datetime.now()
    cron_expr = _dream_cron()

    if not cron_matches(cron_expr, now):
        return

    if _last_dream_run:
        if (now - _last_dream_run).total_seconds() < 90:
            return

    agent_id = _dream_agent_id()
    workspace_dir = session_manager.agent_registry.workspace_dir
    lookback_days = _dream_lookback_days()

    logs = _collect_recent_logs(workspace_dir, agent_id, lookback_days)
    if not logs:
        print("Dreamer: no recent logs to reflect on, skipping")
        return

    print(f"Dreamer: starting dream cycle (reviewing {lookback_days} day(s) of logs)")

    prompt = DREAM_PROMPT_TEMPLATE.format(
        lookback_days=lookback_days,
        logs=logs,
    )

    try:
        response = await session_manager.send_message("dreamer", "dream", prompt)

        response = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL).strip()

        dreams_path = _dreams_dir(workspace_dir, agent_id)
        dreams_path.mkdir(parents=True, exist_ok=True)

        date_str = now.strftime("%Y-%m-%d")
        dream_file = dreams_path / f"{date_str}.md"
        header = f"# Dream Log — {date_str}\n\n"
        dream_file.write_text(header + response)

        _last_dream_run = now
        print(f"Dreamer: dream cycle complete, saved to {dream_file}")

        await ws_manager.broadcast({
            "type": "dream_complete",
            "agent_id": agent_id,
            "date": date_str,
            "preview": response[:300],
            "timestamp": now.isoformat(),
        })
    except Exception as e:
        print(f"Dreamer: dream cycle failed — {e}")
