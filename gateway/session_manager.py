"""
Session Manager - handles session lifecycle and isolation.

Each session represents a unique conversation context across platforms.
Session ID format: {platform}:{user_id}
Example: telegram:123456789, discord:987654321, cli:local
"""

import json
from pathlib import Path
from typing import Dict, Optional, List, AsyncGenerator, Any
from datetime import datetime, timezone
from dataclasses import dataclass, field
from runtime.agent import PersonalAssistant
from runtime.embeddings import EmbeddingsManager

SESSIONS_DIR = Path(__file__).parent.parent / "workspace" / "sessions"


@dataclass
class Session:
    """Represents a conversation session."""

    session_id: str
    platform: str
    user_id: str
    agent: PersonalAssistant
    created_at: datetime = field(default_factory=datetime.now)
    last_active: datetime = field(default_factory=datetime.now)
    message_count: int = 0

    def update_activity(self):
        """Update last active timestamp."""
        self.last_active = datetime.now()
        self.message_count += 1


class SessionManager:
    """Manages multiple conversation sessions with isolation."""

    def __init__(
        self,
        llm_base_url: str = "http://localhost:8321",
        model: str = "redhat-maas/qwen3-14b",
        llm_provider: str = "llamastack",
        llm_api_key: Optional[str] = None,
        llm_extra_headers: Optional[Dict[str, str]] = None
    ):
        self.llm_base_url = llm_base_url
        self.model = model
        self.llm_provider = llm_provider
        self.llm_api_key = llm_api_key
        self.llm_extra_headers = llm_extra_headers
        self.sessions: Dict[str, Session] = {}

        # One shared embeddings manager (and SentenceTransformer model) for
        # every session's agent, instead of loading the model once per
        # session.
        self.embeddings: Optional[EmbeddingsManager] = None
        try:
            self.embeddings = EmbeddingsManager()
            self.embeddings.index_workspace()
        except Exception as e:
            print(f"Warning: Could not initialize shared embeddings: {e}")
            print("Continuing without semantic search...")

    def get_session(self, platform: str, user_id: str) -> Session:
        """
        Get or create a session for a platform/user combination.

        Args:
            platform: Platform name (cli, telegram, discord, etc.)
            user_id: User identifier on that platform

        Returns:
            Session object
        """
        session_id = f"{platform}:{user_id}"

        if session_id not in self.sessions:
            # Create new session with dedicated agent
            agent = PersonalAssistant(
                base_url=self.llm_base_url,
                model=self.model,
                provider=self.llm_provider,
                api_key=self.llm_api_key,
                extra_headers=self.llm_extra_headers,
                embeddings=self.embeddings
            )

            self.sessions[session_id] = Session(
                session_id=session_id,
                platform=platform,
                user_id=user_id,
                agent=agent
            )

        session = self.sessions[session_id]
        session.update_activity()
        return session

    async def send_message(self, platform: str, user_id: str, message: str) -> str:
        """
        Send a message to a session and get response.

        Args:
            platform: Platform name
            user_id: User identifier
            message: User's message

        Returns:
            Agent's response
        """
        session = self.get_session(platform, user_id)
        response = await session.agent.chat(message)
        return response

    async def send_message_stream(
        self, platform: str, user_id: str, message: str
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Yield streaming events from the agent."""
        session = self.get_session(platform, user_id)
        async for event in session.agent.chat_stream(message):
            yield event

    @staticmethod
    def _session_file(session_id: str) -> Path:
        safe_name = session_id.replace(":", "_").replace("/", "_")
        return SESSIONS_DIR / f"{safe_name}.json"

    def save_message(self, session_id: str, role: str, content: str) -> None:
        """Append a message to the session's history on disk."""
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        path = self._session_file(session_id)
        messages = []
        if path.exists():
            try:
                messages = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                messages = []
        messages.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        path.write_text(json.dumps(messages))

    def get_messages(self, session_id: str) -> List[Dict[str, str]]:
        """Load persisted messages for a session."""
        path = self._session_file(session_id)
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return []

    def clear_messages(self, session_id: str) -> None:
        """Delete persisted messages for a session."""
        path = self._session_file(session_id)
        if path.exists():
            path.unlink()

    def list_sessions(self) -> List[Session]:
        """Get all active sessions."""
        return list(self.sessions.values())

    def get_session_stats(self, session_id: str) -> Optional[Dict]:
        """Get statistics for a specific session."""
        if session_id not in self.sessions:
            return None

        session = self.sessions[session_id]
        return {
            "session_id": session.session_id,
            "platform": session.platform,
            "user_id": session.user_id,
            "created_at": session.created_at.isoformat(),
            "last_active": session.last_active.isoformat(),
            "message_count": session.message_count
        }

    def clear_session(self, session_id: str) -> bool:
        """
        Clear a specific session, including any persisted history on disk
        (even if no in-memory session currently exists for it, e.g. after
        a server restart where history was loaded but nothing sent yet).

        Args:
            session_id: Session ID to clear

        Returns:
            True if cleared, False if not found
        """
        existed = session_id in self.sessions
        if existed:
            del self.sessions[session_id]

        had_history = self._session_file(session_id).exists()
        self.clear_messages(session_id)

        return existed or had_history

    def cleanup_inactive_sessions(self, max_age_minutes: int = 60):
        """
        Remove sessions inactive for more than max_age_minutes, including
        their persisted JSON history, and sweep any persisted session files
        left on disk with no matching in-memory session (e.g. from a
        session that aged out on a previous run, or one that was never
        reloaded after a server restart).

        Args:
            max_age_minutes: Maximum age in minutes before cleanup
        """
        now = datetime.now()
        to_remove = []

        for session_id, session in self.sessions.items():
            age_minutes = (now - session.last_active).total_seconds() / 60
            if age_minutes > max_age_minutes:
                to_remove.append(session_id)

        for session_id in to_remove:
            del self.sessions[session_id]
            self.clear_messages(session_id)

        removed_count = len(to_remove)
        removed_count += self._cleanup_orphaned_session_files(max_age_minutes, now)
        return removed_count

    def _cleanup_orphaned_session_files(self, max_age_minutes: float, now: datetime) -> int:
        """Delete persisted session files with no active in-memory session,
        once they're older than max_age_minutes (by file mtime)."""
        if not SESSIONS_DIR.exists():
            return 0

        active_files = {self._session_file(sid) for sid in self.sessions}
        removed = 0

        for path in SESSIONS_DIR.glob("*.json"):
            if path in active_files:
                continue
            age_minutes = (now - datetime.fromtimestamp(path.stat().st_mtime)).total_seconds() / 60
            if age_minutes > max_age_minutes:
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass

        return removed
