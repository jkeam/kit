"""
Memory management for the personal assistant.

Phase 1: Simple file-based memory (no vector search yet).
Phase 3 will add vector embeddings and semantic search.
"""

from pathlib import Path
from datetime import datetime, timedelta
from typing import List


class MemoryManager:
    """Manages long-term memory and daily logs."""

    def __init__(self, workspace_dir: str = "workspace"):
        self.workspace_dir = Path(workspace_dir)
        self.memory_file = self.workspace_dir / "MEMORY.md"
        self.memory_dir = self.workspace_dir / "memory"

        # Ensure directories exist
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.memory_dir.mkdir(parents=True, exist_ok=True)

    def load_context(self) -> str:
        """
        Load memory context to include in agent prompts.

        Returns:
            Combined memory context from MEMORY.md and recent daily logs
        """
        context_parts = []

        # Load long-term memory (MEMORY.md)
        if self.memory_file.exists():
            context_parts.append("# LONG-TERM MEMORY\n")
            context_parts.append(self.memory_file.read_text())

        # Load today's log
        today_log = self._get_daily_log_path(datetime.now())
        if today_log.exists():
            context_parts.append("\n\n# TODAY'S INTERACTIONS\n")
            context_parts.append(today_log.read_text())

        # Load yesterday's log
        yesterday = datetime.now() - timedelta(days=1)
        yesterday_log = self._get_daily_log_path(yesterday)
        if yesterday_log.exists():
            context_parts.append("\n\n# YESTERDAY'S INTERACTIONS\n")
            context_parts.append(yesterday_log.read_text())

        return "\n".join(context_parts) if context_parts else ""

    def log_interaction(self, user_message: str, assistant_response: str):
        """
        Log an interaction to today's daily log.

        Args:
            user_message: The user's message
            assistant_response: The assistant's response
        """
        log_path = self._get_daily_log_path(datetime.now())

        timestamp = datetime.now().strftime("%H:%M:%S")

        entry = f"""
## {timestamp}

**User**: {user_message}

**Assistant**: {assistant_response}

---
"""

        # Append to daily log
        with log_path.open("a") as f:
            if log_path.stat().st_size == 0:
                # First entry of the day - add header
                date_str = datetime.now().strftime("%Y-%m-%d")
                f.write(f"# Daily Log - {date_str}\n\n")

            f.write(entry)

    def _get_daily_log_path(self, date: datetime) -> Path:
        """Get the file path for a daily log."""
        date_str = date.strftime("%Y-%m-%d")
        return self.memory_dir / f"{date_str}.md"

    def get_recent_logs(self, days: int = 7) -> List[str]:
        """
        Get paths to recent daily logs.

        Args:
            days: Number of days to look back

        Returns:
            List of file paths to recent logs
        """
        log_paths = []
        for i in range(days):
            date = datetime.now() - timedelta(days=i)
            log_path = self._get_daily_log_path(date)
            if log_path.exists():
                log_paths.append(str(log_path))

        return log_paths

    def cleanup_old_logs(self, retention_days: int = 7):
        """
        Remove daily logs older than retention period.

        Args:
            retention_days: Number of days to keep
        """
        cutoff_date = datetime.now() - timedelta(days=retention_days)

        for log_file in self.memory_dir.glob("*.md"):
            try:
                # Parse date from filename (YYYY-MM-DD.md)
                date_str = log_file.stem
                log_date = datetime.strptime(date_str, "%Y-%m-%d")

                if log_date < cutoff_date:
                    log_file.unlink()
                    print(f"Removed old log: {log_file.name}")

            except (ValueError, OSError) as e:
                print(f"Warning: Could not process log file {log_file}: {e}")
