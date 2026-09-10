"""
Memory management for the personal assistant.

Each agent gets its own isolated memory directory under
workspace/memory/{agent_id}/ containing daily logs and a per-agent
MEMORY.md. Broadcasts live in workspace/memory/broadcasts/ and are
loaded into every agent's context.
"""

from pathlib import Path
from datetime import datetime, timedelta
from typing import List


class MemoryManager:
    """Manages per-agent memory (daily logs + long-term) and shared broadcasts."""

    def __init__(self, workspace_dir: str = "workspace", agent_id: str = "kit"):
        self.workspace_dir = Path(workspace_dir)
        self.agent_id = agent_id

        self.agent_memory_dir = self.workspace_dir / "memory" / agent_id
        self.memory_file = self.agent_memory_dir / "MEMORY.md"
        self.broadcast_dir = self.workspace_dir / "memory" / "broadcasts"

        self.agent_memory_dir.mkdir(parents=True, exist_ok=True)
        self.broadcast_dir.mkdir(parents=True, exist_ok=True)

    def load_context(self) -> str:
        """
        Load memory context to include in agent prompts.

        Returns:
            Combined context from this agent's MEMORY.md, its own daily logs,
            and shared team broadcasts.
        """
        context_parts = []

        if self.memory_file.exists():
            context_parts.append("# LONG-TERM MEMORY\n")
            context_parts.append(self.memory_file.read_text())

        today_log = self._get_daily_log_path(datetime.now())
        if today_log.exists():
            context_parts.append("\n\n# TODAY'S INTERACTIONS\n")
            context_parts.append(today_log.read_text())

        yesterday = datetime.now() - timedelta(days=1)
        yesterday_log = self._get_daily_log_path(yesterday)
        if yesterday_log.exists():
            context_parts.append("\n\n# YESTERDAY'S INTERACTIONS\n")
            context_parts.append(yesterday_log.read_text())

        broadcast_context = self._load_broadcasts()
        if broadcast_context:
            context_parts.append("\n\n# TEAM BROADCASTS\n")
            context_parts.append(broadcast_context)

        return "\n".join(context_parts) if context_parts else ""

    def log_interaction(self, user_message: str, assistant_response: str, speaker: str = "Assistant"):
        """
        Log an interaction to this agent's daily log.

        Args:
            user_message: The user's message
            assistant_response: The assistant's response
            speaker: Who produced the response
        """
        log_path = self._get_daily_log_path(datetime.now())
        timestamp = datetime.now().strftime("%H:%M:%S")

        entry = f"""
## {timestamp}

**User**: {user_message}

**{speaker}**: {assistant_response}

---
"""

        with log_path.open("a") as f:
            if log_path.stat().st_size == 0:
                date_str = datetime.now().strftime("%Y-%m-%d")
                f.write(f"# Daily Log - {date_str}\n\n")
            f.write(entry)

    def save_broadcast(self, message: str):
        """Append a broadcast message to today's broadcast log."""
        log_path = self._get_broadcast_log_path(datetime.now())
        timestamp = datetime.now().strftime("%H:%M:%S")

        entry = f"""
## {timestamp}

{message}

---
"""

        with log_path.open("a") as f:
            if log_path.stat().st_size == 0:
                date_str = datetime.now().strftime("%Y-%m-%d")
                f.write(f"# Team Broadcasts - {date_str}\n\n")
            f.write(entry)

    def get_broadcasts(self, date: str = None) -> str:
        """Read broadcast log for a given date (default: today)."""
        if date is None:
            target = datetime.now()
        else:
            target = datetime.strptime(date, "%Y-%m-%d")

        log_path = self._get_broadcast_log_path(target)
        if log_path.exists():
            return log_path.read_text()
        return ""

    def _load_broadcasts(self) -> str:
        """Load today and yesterday's broadcasts for system prompt context."""
        parts = []

        today_broadcast = self._get_broadcast_log_path(datetime.now())
        if today_broadcast.exists():
            parts.append(today_broadcast.read_text())

        yesterday = datetime.now() - timedelta(days=1)
        yesterday_broadcast = self._get_broadcast_log_path(yesterday)
        if yesterday_broadcast.exists():
            parts.append(yesterday_broadcast.read_text())

        return "\n".join(parts) if parts else ""

    def _get_daily_log_path(self, date: datetime) -> Path:
        """Get the file path for this agent's daily log."""
        date_str = date.strftime("%Y-%m-%d")
        return self.agent_memory_dir / f"{date_str}.md"

    def _get_broadcast_log_path(self, date: datetime) -> Path:
        """Get the file path for a broadcast log."""
        date_str = date.strftime("%Y-%m-%d")
        return self.broadcast_dir / f"{date_str}.md"

    def get_recent_logs(self, days: int = 7) -> List[str]:
        """Get paths to this agent's recent daily logs."""
        log_paths = []
        for i in range(days):
            date = datetime.now() - timedelta(days=i)
            log_path = self._get_daily_log_path(date)
            if log_path.exists():
                log_paths.append(str(log_path))
        return log_paths

    def cleanup_old_logs(self, retention_days: int = 7):
        """Remove daily logs older than retention period, across all agents."""
        cutoff_date = datetime.now() - timedelta(days=retention_days)
        memory_root = self.workspace_dir / "memory"

        for log_file in memory_root.glob("**/*.md"):
            if log_file.name == "MEMORY.md":
                continue
            try:
                date_str = log_file.stem
                log_date = datetime.strptime(date_str, "%Y-%m-%d")
                if log_date < cutoff_date:
                    log_file.unlink()
                    print(f"Removed old log: {log_file}")
            except (ValueError, OSError):
                pass
