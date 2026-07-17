"""
Session Manager - handles session lifecycle and isolation.

Each session represents a unique conversation context across platforms.
Session ID format: {platform}:{user_id}
Example: telegram:123456789, discord:987654321, cli:local
"""

from typing import Dict, Optional, List
from datetime import datetime
from dataclasses import dataclass, field
from runtime.agent import PersonalAssistant


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
        llama_stack_url: str = "http://localhost:8321",
        model: str = "redhat-maas/qwen3-14b"
    ):
        """
        Initialize session manager.

        Args:
            llama_stack_url: LlamaStack server URL
            model: Model to use for agents
        """
        self.llama_stack_url = llama_stack_url
        self.model = model
        self.sessions: Dict[str, Session] = {}

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
                base_url=self.llama_stack_url,
                model=self.model
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

    def send_message(self, platform: str, user_id: str, message: str) -> str:
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
        response = session.agent.chat(message)
        return response

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
        Clear a specific session.

        Args:
            session_id: Session ID to clear

        Returns:
            True if cleared, False if not found
        """
        if session_id in self.sessions:
            del self.sessions[session_id]
            return True
        return False

    def cleanup_inactive_sessions(self, max_age_minutes: int = 60):
        """
        Remove sessions inactive for more than max_age_minutes.

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

        return len(to_remove)
