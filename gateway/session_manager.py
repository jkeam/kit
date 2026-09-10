"""
Session Manager - handles session lifecycle and isolation.

Each session represents a unique conversation context across platforms.
Session ID format: {platform}:{user_id}
Example: telegram:123456789, discord:987654321, cli:local
"""

import json
from collections import deque
from pathlib import Path
from typing import Callable, Dict, Optional, List, AsyncGenerator, Any, Awaitable
from datetime import datetime, timezone
from dataclasses import dataclass, field
from runtime.agent import PersonalAssistant
from runtime.agents import KIT_AGENT_ID, AgentRegistry
from runtime.embeddings import EmbeddingsManager

# How many recent cross-agent activity entries (tool calls, delegation,
# status changes) to keep for the "Team Activity" inspection view.
ACTIVITY_BUFFER_SIZE = 500


def _truncate(text: str, limit: int = 140) -> str:
    text = " ".join(text.strip().split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _describe_tool_call(tool_name: str, tool_args: Dict[str, Any]) -> str:
    args_preview = ", ".join(f"{k}={v!r}" for k, v in list(tool_args.items())[:2])
    return _truncate(f"{tool_name}({args_preview})")


def make_session_id(platform: str, user_id: str, agent_id: str = KIT_AGENT_ID) -> str:
    """Kit keeps the original `{platform}:{user_id}` session id (no
    migration needed for existing history); any other agent gets its own
    `{platform}:{user_id}:{agent_id}` thread with the same user, so
    switching the chat target moves between separately-remembered
    conversations rather than overwriting one shared thread."""
    if agent_id == KIT_AGENT_ID:
        return f"{platform}:{user_id}"
    return f"{platform}:{user_id}:{agent_id}"


@dataclass
class Session:
    """Represents a conversation session."""

    session_id: str
    platform: str
    user_id: str
    agent_id: str
    agent: PersonalAssistant
    created_at: datetime = field(default_factory=datetime.now)
    last_active: datetime = field(default_factory=datetime.now)

    def update_activity(self):
        """Update last active timestamp."""
        self.last_active = datetime.now()


class SessionManager:
    """Manages multiple conversation sessions with isolation."""

    def __init__(
        self,
        llm_base_url: str = "http://localhost:8321",
        model: str = "redhat-maas/qwen3-14b",
        llm_provider: str = "llamastack",
        llm_api_key: Optional[str] = None,
        llm_extra_headers: Optional[Dict[str, str]] = None,
        agent_registry: Optional[AgentRegistry] = None,
        on_event: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ):
        self.llm_base_url = llm_base_url
        self.model = model
        self.llm_provider = llm_provider
        self.llm_api_key = llm_api_key
        self.llm_extra_headers = llm_extra_headers
        self.sessions: Dict[str, Session] = {}
        self.agent_registry = agent_registry or AgentRegistry()
        # Persisted chat history lives under the same workspace the agent
        # registry uses - tied together so tests/alternate workspaces never
        # touch the real workspace/sessions/ directory by accident.
        self.sessions_dir = self.agent_registry.workspace_dir / "sessions"

        # Live busy/idle + "what are they doing" status per agent_id, plus a
        # capped cross-agent activity feed (tool calls, delegation, status
        # changes) for the "Team Activity" inspection view. `on_event`, when
        # given, is called with every new activity entry (e.g. bound to
        # ConnectionManager.broadcast) so the UI updates live.
        self.agent_status: Dict[str, Dict[str, Any]] = {}
        self.activity: "deque[Dict[str, Any]]" = deque(maxlen=ACTIVITY_BUFFER_SIZE)
        self.on_event = on_event

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

    def get_session(self, platform: str, user_id: str, agent_id: str = KIT_AGENT_ID) -> Session:
        """
        Get or create a session for a platform/user/agent combination.

        Each (platform, user, agent) triple gets its own persistent
        conversation thread (see `make_session_id`) - the agent bound to a
        session is fixed at creation, same as platform/user_id.

        Args:
            platform: Platform name (cli, telegram, discord, etc.)
            user_id: User identifier on that platform
            agent_id: Which team member this thread talks to (default "kit")

        Returns:
            Session object
        """
        session_id = make_session_id(platform, user_id, agent_id)

        if session_id not in self.sessions:
            defn = self.agent_registry.resolve(agent_id)
            if defn is None:
                raise ValueError(f"Unknown agent '{agent_id}'")

            agent = PersonalAssistant(
                base_url=self.llm_base_url,
                model=defn.model or self.model,
                provider=defn.provider or self.llm_provider,
                workspace_dir=str(self.agent_registry.workspace_dir),
                api_key=self.llm_api_key,
                extra_headers=self.llm_extra_headers,
                embeddings=self.embeddings,
                allowed_tools=defn.allowed_tools,
                allowed_skills=defn.allowed_skills,
                soul_override=None if agent_id == KIT_AGENT_ID else defn.soul,
                agent_id=agent_id,
                session_manager=self,
                platform=platform,
                user_id=user_id,
            )

            self.sessions[session_id] = Session(
                session_id=session_id,
                platform=platform,
                user_id=user_id,
                agent_id=agent_id,
                agent=agent
            )

        session = self.sessions[session_id]
        session.update_activity()
        return session

    async def send_message(
        self, platform: str, user_id: str, message: str, agent_id: str = KIT_AGENT_ID
    ) -> str:
        """
        Send a message to a session and get response.

        Args:
            platform: Platform name
            user_id: User identifier
            message: User's message
            agent_id: Which team member to address (default "kit")

        Returns:
            Agent's response
        """
        session = self.get_session(platform, user_id, agent_id)
        full_response = ""
        async for event in self._run_and_track(session, message):
            if event["type"] == "stream_end":
                full_response = event["content"]
            elif event["type"] == "stream_error":
                full_response = f"Error: {event['error']}"
        return full_response

    async def send_message_stream(
        self, platform: str, user_id: str, message: str, agent_id: str = KIT_AGENT_ID
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Yield streaming events from the agent."""
        session = self.get_session(platform, user_id, agent_id)
        async for event in self._run_and_track(session, message):
            yield event

    async def delegate(
        self,
        platform: str,
        user_id: str,
        from_agent_id: str,
        to_agent_id: str,
        task: str,
        depth: int = 0,
    ) -> str:
        """
        Run a task on another agent's own persistent thread (for this same
        user) on behalf of a delegating agent, and return its reply.

        Called from PersonalAssistant._delegate (runtime/agent.py) when an
        agent invokes the agent_delegate tool - this is what makes the
        delegated instruction land in the exact thread the user would see
        if they switched their chat target to `to_agent_id`.
        """
        session = self.get_session(platform, user_id, to_agent_id)
        self.save_message(session.session_id, "user", task, sender=from_agent_id)
        if self.on_event:
            await self.on_event({
                "type": "user_message",
                "session_id": session.session_id,
                "platform": platform,
                "agent_id": to_agent_id,
                "sender": from_agent_id,
                "message": task,
            })

        full_response = ""
        async for event in self._run_and_track(session, task, depth=depth):
            if event["type"] == "stream_end":
                full_response = event["content"]
            elif event["type"] == "stream_error":
                full_response = f"Error: {event['error']}"
        self.save_message(session.session_id, "assistant", full_response)

        if self.on_event:
            await self.on_event({
                "type": "assistant_message",
                "session_id": session.session_id,
                "platform": platform,
                "agent_id": to_agent_id,
                "via_delegation": True,
                "message": full_response,
            })

        return full_response

    async def _run_and_track(
        self, session: "Session", message: str, depth: int = 0
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Run one turn on `session.agent`, updating live status (busy while
        running, idle when done, with a "what is it doing" description that
        gets more specific per tool call) and recording every tool-call
        event into the cross-agent activity feed - regardless of whether
        the caller is a direct chat, a stream, or a delegated task."""
        await self._set_status(session.agent_id, "busy", _truncate(message), session.session_id)
        try:
            async for event in session.agent.chat_stream(message, _delegation_depth=depth):
                if event["type"] in ("tool_call_start", "tool_call_result"):
                    await self._record({
                        "event_type": event["type"],
                        "session_id": session.session_id,
                        "agent_id": session.agent_id,
                        "detail": {k: v for k, v in event.items() if k != "type"},
                    })
                if event["type"] == "tool_call_start":
                    description = _describe_tool_call(event.get("tool_name", ""), event.get("tool_args") or {})
                    await self._set_status(session.agent_id, "busy", description, session.session_id)
                yield event
        finally:
            await self._set_status(session.agent_id, "idle", None, session.session_id)

    async def _record(self, event: Dict[str, Any]) -> None:
        """Append an entry to the activity feed and fan it out live."""
        entry = {"timestamp": datetime.now(timezone.utc).isoformat(), **event}
        self.activity.append(entry)
        if self.on_event:
            try:
                await self.on_event(entry)
            except Exception:
                pass

    async def _set_status(
        self, agent_id: str, status: str, current_task: Optional[str], session_id: Optional[str] = None
    ) -> None:
        entry = {
            "agent_id": agent_id,
            "status": status,
            "current_task": current_task,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.agent_status[agent_id] = entry
        await self._record({"event_type": "agent_status", "session_id": session_id, **entry})

    def get_agent_status(self) -> Dict[str, Dict[str, Any]]:
        """Current busy/idle + current-task snapshot for every agent that
        has run at least once (for initial page load, before any WS events
        have arrived)."""
        return dict(self.agent_status)

    def get_activity(self, agent_id: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
        """The most recent cross-agent activity entries, optionally
        filtered to one agent."""
        items = list(self.activity)
        if agent_id:
            items = [e for e in items if e.get("agent_id") == agent_id]
        return items[-limit:]

    def _session_file(self, session_id: str) -> Path:
        safe_name = session_id.replace(":", "_").replace("/", "_")
        return self.sessions_dir / f"{safe_name}.json"

    def save_message(
        self, session_id: str, role: str, content: str, sender: Optional[str] = None
    ) -> None:
        """Append a message to the session's history on disk.

        `sender` is only set when this message was placed into someone
        else's thread by another agent (delegation) rather than typed by
        the human - lets the UI render "Kit asked: ..." instead of
        implying the human wrote it.
        """
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        path = self._session_file(session_id)
        messages = []
        if path.exists():
            try:
                messages = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                messages = []
        entry = {
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if sender is not None:
            entry["sender"] = sender
        messages.append(entry)
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

    @staticmethod
    def _parse_session_id(session_id: str) -> tuple[str, str, str]:
        """Recover (platform, user_id, agent_id) from a session id built by
        make_session_id, for sessions that only exist on disk (no live
        in-memory Session to read these off of)."""
        parts = session_id.split(":")
        if len(parts) >= 3:
            return parts[0], parts[1], parts[2]
        if len(parts) == 2:
            return parts[0], parts[1], KIT_AGENT_ID
        return "unknown", session_id, KIT_AGENT_ID

    def _persisted_session_ids(self) -> List[str]:
        """Session ids with history on disk, recovered from session file
        names (see _session_file - ':' becomes '_')."""
        if not self.sessions_dir.exists():
            return []
        return [p.stem.replace("_", ":") for p in self.sessions_dir.glob("*.json")]

    def list_sessions(self) -> List[Dict[str, Any]]:
        """All sessions with any state: live in-memory ones plus
        persisted-only ones whose history survived a restart but haven't
        been re-activated by a new message yet."""
        ids = set(self.sessions.keys()) | set(self._persisted_session_ids())
        stats = (self.get_session_stats(sid) for sid in ids)
        return [s for s in stats if s is not None]

    def get_session_stats(self, session_id: str) -> Optional[Dict]:
        """Get statistics for a specific session - live (in-memory) or
        persisted-only (history on disk from before the last restart)."""
        messages = self.get_messages(session_id)

        if session_id in self.sessions:
            session = self.sessions[session_id]
            return {
                "session_id": session.session_id,
                "platform": session.platform,
                "user_id": session.user_id,
                "agent_id": session.agent_id,
                "created_at": session.created_at.isoformat(),
                "last_active": session.last_active.isoformat(),
                "message_count": len(messages)
            }

        if not messages:
            return None

        platform, user_id, agent_id = self._parse_session_id(session_id)
        return {
            "session_id": session_id,
            "platform": platform,
            "user_id": user_id,
            "agent_id": agent_id,
            "created_at": messages[0]["timestamp"],
            "last_active": messages[-1]["timestamp"],
            "message_count": len(messages)
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
        if not self.sessions_dir.exists():
            return 0

        active_files = {self._session_file(sid) for sid in self.sessions}
        removed = 0

        for path in self.sessions_dir.glob("*.json"):
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
