"""
Gateway Server - FastAPI server for multi-platform message routing.

Handles:
- HTTP API for channel communication
- Session management
- Message routing to agent runtime
- Event broadcasting (future: WebSockets)
"""

from dotenv import load_dotenv
load_dotenv()

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Header, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any, Optional, Set, Union, Literal
from pathlib import Path
from datetime import datetime, timezone
import uvicorn
import json
import asyncio
import base64
import secrets
import re
import uuid
import random

import os
from env_config import env_int
from gateway.session_manager import SessionManager, make_session_id
from gateway.rate_limit import check_rate_limit
from gateway.scheduler import run_scheduler
from gateway.dreamer import run_dream_cycle, list_dreams, get_dream
from runtime.memory import MemoryManager
from runtime.agent import OPENAI_COMPATIBLE_PROVIDERS


def _require_gateway_token(authorization: Optional[str] = Header(default=None)) -> None:
    """Dependency that gates HTTP routes behind GATEWAY_TOKEN.

    No-op (open access) when GATEWAY_TOKEN is unset, preserving the
    zero-config local/dev experience. When it's set, requires a matching
    `Authorization: Bearer <token>` header.
    """
    token = os.environ.get("GATEWAY_TOKEN")
    if not token:
        return

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")

    provided = authorization[len("Bearer "):]
    if not secrets.compare_digest(provided, token):
        raise HTTPException(status_code=401, detail="Invalid gateway token")


WS_TOKEN_SUBPROTOCOL_PREFIX = "kit-token."


def _decode_ws_token_subprotocol(value: str) -> Optional[str]:
    """Decode a `kit-token.<base64url>` WebSocket subprotocol value back to
    the raw token string, or None if `value` isn't in that form."""
    if not value.startswith(WS_TOKEN_SUBPROTOCOL_PREFIX):
        return None
    encoded = value[len(WS_TOKEN_SUBPROTOCOL_PREFIX):]
    padded = encoded + "=" * (-len(encoded) % 4)
    try:
        return base64.urlsafe_b64decode(padded).decode("utf-8")
    except Exception:
        return None


def _websocket_auth(websocket: WebSocket) -> tuple[bool, Optional[str]]:
    """Validate GATEWAY_TOKEN for a WebSocket handshake.

    Returns (is_valid, matched_subprotocol). Two ways for the token to
    travel, checked in preference order:

    1. `Sec-WebSocket-Protocol: kit-token.<base64url token>` - a real
       handshake header, not part of the URL, so it doesn't end up in
       access logs, proxy logs, or browser history the way a query param
       does. When this is how auth succeeded, `matched_subprotocol` must be
       echoed back in `websocket.accept(subprotocol=...)`.
    2. `?token=...` query param - kept for backward compatibility, since
       not every WS client can set a custom subprotocol.
    """
    token = os.environ.get("GATEWAY_TOKEN")
    if not token:
        return True, None

    for offered in websocket.scope.get("subprotocols", []):
        decoded = _decode_ws_token_subprotocol(offered)
        if decoded is not None and secrets.compare_digest(decoded, token):
            return True, offered

    provided = websocket.query_params.get("token")
    if provided and secrets.compare_digest(provided, token):
        return True, None

    return False, None


def _now() -> str:
    """Wall-clock UTC timestamp for event payloads (not monotonic time)."""
    return datetime.now(timezone.utc).isoformat()


# Request/Response models
class ChatRequest(BaseModel):
    """Request format for chat endpoint."""
    platform: str
    user_id: str
    message: str
    agent_id: str = "kit"
    metadata: Optional[Dict[str, Any]] = None


class BroadcastRequest(BaseModel):
    """Request format for broadcast endpoint."""
    platform: str
    user_id: str
    message: str


class ChatResponse(BaseModel):
    """Response format for chat endpoint."""
    response: str
    session_id: str
    message_count: int


class SessionStats(BaseModel):
    """Session statistics."""
    session_id: str
    platform: str
    user_id: str
    agent_id: str
    created_at: str
    last_active: str
    message_count: int


# Global session manager (initialized on startup)
session_manager: Optional[SessionManager] = None

# WebSocket connection manager
class ConnectionManager:
    """Manages WebSocket connections."""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket, subprotocol: Optional[str] = None):
        await websocket.accept(subprotocol=subprotocol)
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)

    async def broadcast(self, message: dict):
        """Broadcast message to all connected clients."""
        disconnected = set()
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.add(connection)

        # Clean up disconnected clients
        self.active_connections -= disconnected

manager = ConnectionManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize gateway on startup, clean up on shutdown."""
    global session_manager
    base_url = os.environ.get("LLM_BASE_URL", "http://localhost:8321")
    model = os.environ.get("LLM_MODEL", "redhat-maas/qwen3-14b")
    provider = os.environ.get("LLM_PROVIDER", "llamastack")
    api_key = os.environ.get("LLM_API_KEY")
    extra_headers_raw = os.environ.get("LLM_EXTRA_HEADERS")
    extra_headers = json.loads(extra_headers_raw) if extra_headers_raw else None
    from runtime.providers import ProviderRegistry
    provider_registry = ProviderRegistry()
    provider_registry.ensure_default_provider()
    session_manager = SessionManager(
        llm_base_url=base_url,
        model=model,
        llm_provider=provider,
        llm_api_key=api_key,
        llm_extra_headers=extra_headers,
        on_event=manager.broadcast,
        provider_registry=provider_registry,
    )
    scheduler_task = asyncio.create_task(run_scheduler(session_manager, manager))
    dreamer_task = asyncio.create_task(run_dream_cycle(session_manager, manager))
    providers = provider_registry.list_providers()
    print("✅ Gateway server started")
    print(f"🤖 LLM: {model} at {base_url} (provider={provider})")
    if providers:
        print(f"📦 {len(providers)} provider(s) configured: {', '.join(p.id for p in providers)}")
    print("⏰ Scheduler running (60s check interval)")
    print("💤 Dream cycle running (cron: {})".format(os.environ.get("DREAM_CRON", "0 3 * * *")))
    print("📡 Ready to handle multi-platform requests")

    yield

    scheduler_task.cancel()
    dreamer_task.cancel()
    print("🛑 Gateway server shutting down")


# Create FastAPI app
app = FastAPI(
    title="Kit Gateway",
    description="Gateway for Kit - Your AI Toolkit",
    version="0.1.0",
    lifespan=lifespan
)

# Add CORS middleware. Defaults to "*" for local/dev use; set CORS_ORIGINS to
# a comma-separated list of origins to restrict this in production.
_cors_origins_raw = os.environ.get("CORS_ORIGINS", "*")
_cors_origins = (
    ["*"] if _cors_origins_raw.strip() == "*"
    else [origin.strip() for origin in _cors_origins_raw.split(",") if origin.strip()]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files for web UI
web_dir = Path(__file__).parent.parent / "web"
if web_dir.exists():
    app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")


@app.get("/")
async def root():
    """Root endpoint - redirect to web UI."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/static/index.html")


@app.get("/chat/{agent_id:path}")
async def chat_spa_fallback(agent_id: str):
    """SPA fallback - serve index.html for /chat/* URLs so pushState routes
    work on page reload and direct navigation."""
    from fastapi.responses import FileResponse
    index = Path(__file__).parent.parent / "web" / "index.html"
    if index.exists():
        return FileResponse(str(index))
    raise HTTPException(status_code=404, detail="Web UI not found")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "active_sessions": len(session_manager.sessions) if session_manager else 0
    }


@app.get("/config")
async def get_config():
    """Runtime-tunable settings the web UI reads on load, so they can be
    changed via env vars without editing static JS.

    Deliberately left open (no _require_gateway_token dependency) — the web
    UI needs `auth_required` before it knows whether it has to prompt for a
    token in the first place.
    """
    return {
        "ws_max_reconnect_attempts": env_int("WS_MAX_RECONNECT_ATTEMPTS", 5),
        "auth_required": bool(os.environ.get("GATEWAY_TOKEN")),
    }


@app.post("/chat", response_model=ChatResponse, dependencies=[Depends(_require_gateway_token)])
async def chat(request: ChatRequest):
    """
    Send a message to the assistant.

    Args:
        request: ChatRequest with platform, user_id, and message

    Returns:
        ChatResponse with assistant's response
    """
    if not session_manager:
        raise HTTPException(status_code=500, detail="Session manager not initialized")

    if not check_rate_limit(f"{request.platform}:{request.user_id}"):
        raise HTTPException(status_code=429, detail="Rate limit exceeded, try again shortly")

    try:
        session_id = make_session_id(request.platform, request.user_id, request.agent_id)

        session_manager.save_message(session_id, "user", request.message)

        # Broadcast user message event
        await manager.broadcast({
            "type": "user_message",
            "session_id": session_id,
            "platform": request.platform,
            "agent_id": request.agent_id,
            "message": request.message,
            "timestamp": _now()
        })

        # Send message through session manager
        response = await session_manager.send_message(
            platform=request.platform,
            user_id=request.user_id,
            message=request.message,
            agent_id=request.agent_id
        )

        # Get session stats
        stats = session_manager.get_session_stats(session_id)

        session_manager.save_message(session_id, "assistant", response)

        # Broadcast assistant response event
        await manager.broadcast({
            "type": "assistant_message",
            "session_id": session_id,
            "platform": request.platform,
            "agent_id": request.agent_id,
            "message": response,
            "message_count": stats["message_count"] if stats else 0,
            "timestamp": _now()
        })

        return ChatResponse(
            response=response,
            session_id=session_id,
            message_count=stats["message_count"] if stats else 0
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing message: {str(e)}")


@app.post("/chat/stream", dependencies=[Depends(_require_gateway_token)])
async def chat_stream(request: ChatRequest):
    """Stream assistant responses as Server-Sent Events."""
    if not session_manager:
        raise HTTPException(status_code=500, detail="Session manager not initialized")

    if not check_rate_limit(f"{request.platform}:{request.user_id}"):
        raise HTTPException(status_code=429, detail="Rate limit exceeded, try again shortly")

    session_id = make_session_id(request.platform, request.user_id, request.agent_id)
    session_manager.save_message(session_id, "user", request.message)

    await manager.broadcast({
        "type": "user_message",
        "session_id": session_id,
        "platform": request.platform,
        "agent_id": request.agent_id,
        "message": request.message,
        "timestamp": _now()
    })

    async def event_generator():
        full_response = ""
        try:
            async for event in session_manager.send_message_stream(
                platform=request.platform,
                user_id=request.user_id,
                message=request.message,
                agent_id=request.agent_id,
            ):
                if event["type"] == "stream_end":
                    full_response = event.get("content", "")
                    session_manager.save_message(session_id, "assistant", full_response)
                    stats = session_manager.get_session_stats(session_id)
                    message_count = stats["message_count"] if stats else 0
                    event = {**event, "message_count": message_count}
                    yield f"data: {json.dumps(event)}\n\n"
                    await manager.broadcast({
                        "type": "assistant_message",
                        "session_id": session_id,
                        "platform": request.platform,
                        "agent_id": request.agent_id,
                        "message": full_response,
                        "message_count": message_count,
                        "timestamp": _now(),
                    })
                else:
                    yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'stream_error', 'error': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _broadcast_session_id(platform: str, user_id: str) -> str:
    return f"broadcast:{platform}:{user_id}"


@app.post("/broadcast", dependencies=[Depends(_require_gateway_token)])
async def broadcast_message(request: BroadcastRequest):
    """Post a broadcast message visible to all agents."""
    if not session_manager:
        raise HTTPException(status_code=500, detail="Session manager not initialized")

    if not check_rate_limit(f"broadcast:{request.platform}:{request.user_id}"):
        raise HTTPException(status_code=429, detail="Rate limit exceeded, try again shortly")

    mem = MemoryManager(str(session_manager.agent_registry.workspace_dir))
    mem.save_broadcast(request.message)

    msg_id = str(uuid.uuid4())
    session_id = _broadcast_session_id(request.platform, request.user_id)
    session_manager.save_message(session_id, "user", request.message, message_id=msg_id)

    await manager.broadcast({
        "type": "user_message",
        "session_id": session_id,
        "platform": request.platform,
        "message": request.message,
        "message_id": msg_id,
        "timestamp": _now(),
    })

    asyncio.create_task(_generate_broadcast_reactions(request.message, msg_id, session_id))
    asyncio.create_task(_generate_broadcast_replies(request.message, session_id))

    return {"status": "ok", "session_id": session_id}


@app.get("/sessions", response_model=List[SessionStats], dependencies=[Depends(_require_gateway_token)])
async def list_sessions():
    """List all sessions - live ones plus persisted-only ones whose history
    survived a restart but hasn't been re-activated by a new message yet."""
    if not session_manager:
        raise HTTPException(status_code=500, detail="Session manager not initialized")

    sessions = session_manager.list_sessions()
    return [
        SessionStats(**s)
        for s in sessions
    ]


@app.get("/sessions/{session_id}", dependencies=[Depends(_require_gateway_token)])
async def get_session(session_id: str):
    """Get specific session details."""
    if not session_manager:
        raise HTTPException(status_code=500, detail="Session manager not initialized")

    stats = session_manager.get_session_stats(session_id)
    if not stats:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    return stats


@app.delete("/sessions/{session_id}", dependencies=[Depends(_require_gateway_token)])
async def clear_session(session_id: str):
    """Clear a specific session."""
    if not session_manager:
        raise HTTPException(status_code=500, detail="Session manager not initialized")

    success = session_manager.clear_session(session_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    return {"message": f"Session {session_id} cleared"}


@app.post("/sessions/cleanup", dependencies=[Depends(_require_gateway_token)])
async def cleanup_sessions(max_age_minutes: int = 60):
    """Cleanup inactive sessions."""
    if not session_manager:
        raise HTTPException(status_code=500, detail="Session manager not initialized")

    removed = session_manager.cleanup_inactive_sessions(max_age_minutes)
    return {
        "message": f"Cleaned up {removed} inactive sessions",
        "removed_count": removed
    }


SCHEDULES_PATH = Path(__file__).parent.parent / "workspace" / "schedules" / "schedules.json"


@app.get("/schedules", dependencies=[Depends(_require_gateway_token)])
async def list_schedules():
    """List all scheduled tasks."""
    if not SCHEDULES_PATH.exists():
        return []
    schedules = json.loads(SCHEDULES_PATH.read_text())
    return schedules


@app.get("/dreams", dependencies=[Depends(_require_gateway_token)])
async def list_dream_logs(agent_id: str = "kit"):
    """List all dream logs for an agent, newest first."""
    sm = _require_session_manager()
    return list_dreams(sm.agent_registry.workspace_dir, agent_id)


@app.get("/dreams/{date}", dependencies=[Depends(_require_gateway_token)])
async def get_dream_log(date: str, agent_id: str = "kit"):
    """Get a specific dream log by date (YYYY-MM-DD)."""
    sm = _require_session_manager()
    content = get_dream(sm.agent_registry.workspace_dir, agent_id, date)
    if content is None:
        raise HTTPException(status_code=404, detail=f"No dream log for {date}")
    return {"date": date, "agent_id": agent_id, "content": content}


SOUL_PATH = Path(__file__).parent.parent / "workspace" / "SOUL.md"


@app.get("/persona", dependencies=[Depends(_require_gateway_token)])
async def get_persona():
    """Get the current SOUL.md content."""
    content = SOUL_PATH.read_text() if SOUL_PATH.exists() else ""
    return {"content": content}


class PersonaUpdate(BaseModel):
    content: str


@app.get("/sessions/{session_id}/messages", dependencies=[Depends(_require_gateway_token)])
async def get_session_messages(session_id: str):
    """Get persisted message history for a session."""
    if not session_manager:
        raise HTTPException(status_code=500, detail="Session manager not initialized")
    return session_manager.get_messages(session_id)


@app.put("/persona", dependencies=[Depends(_require_gateway_token)])
async def update_persona(update: PersonaUpdate):
    """Update SOUL.md content."""
    SOUL_PATH.parent.mkdir(parents=True, exist_ok=True)
    SOUL_PATH.write_text(update.content)
    return {"message": "Persona updated"}


# --- Team of agents: roster CRUD, templates, and live status/activity ---

class AgentOut(BaseModel):
    """A fully-resolved team member (persona text included)."""
    id: str
    name: str
    description: str
    template_id: Optional[str] = None
    tools: Any
    skills: Any
    model: Optional[str] = None
    provider: Optional[str] = None
    soul: str
    mcp_servers: Optional[Dict[str, Any]] = None
    color: Optional[str] = None

    @staticmethod
    def from_definition(defn) -> "AgentOut":
        return AgentOut(
            id=defn.id, name=defn.name, description=defn.description,
            template_id=defn.template_id, tools=defn.tools, skills=defn.skills,
            model=defn.model, provider=defn.provider, soul=defn.soul,
            mcp_servers=defn.mcp_servers, color=defn.color,
        )


class CreateAgentRequest(BaseModel):
    template_id: str
    id: str
    name: Optional[str] = None
    description: Optional[str] = None
    tools: Optional[Union[List[str], Literal["*"]]] = None
    skills: Optional[Union[List[str], Literal["*"]]] = None
    soul: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    mcp_servers: Optional[Dict[str, Any]] = None
    color: Optional[str] = None


class UpdateAgentRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    tools: Optional[Union[List[str], Literal["*"]]] = None
    skills: Optional[Union[List[str], Literal["*"]]] = None
    soul: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    mcp_servers: Optional[Dict[str, Any]] = None
    color: Optional[str] = None


def _require_session_manager() -> SessionManager:
    if not session_manager:
        raise HTTPException(status_code=500, detail="Session manager not initialized")
    return session_manager


@app.get("/agents", response_model=List[AgentOut], dependencies=[Depends(_require_gateway_token)])
async def list_agents():
    """List every configured team member (Kit first)."""
    sm = _require_session_manager()
    return [AgentOut.from_definition(a) for a in sm.agent_registry.list_agents()]


@app.get("/agents/status", dependencies=[Depends(_require_gateway_token)])
async def get_agents_status():
    """Current busy/idle + current-task snapshot for every agent that has
    run at least once - for initial page load, before any `agent_status`
    WS events have arrived."""
    sm = _require_session_manager()
    return sm.get_agent_status()


@app.get("/agents/activity", dependencies=[Depends(_require_gateway_token)])
async def get_agents_activity(agent_id: Optional[str] = None, limit: int = 200):
    """The cross-agent activity feed (tool calls, delegation, status
    changes) - the "inspect all agent-to-agent communication" view."""
    sm = _require_session_manager()
    return sm.get_activity(agent_id=agent_id, limit=limit)


@app.post("/agents", response_model=AgentOut, dependencies=[Depends(_require_gateway_token)])
async def create_agent(request: CreateAgentRequest):
    """Create a team member from a template, with optional overrides."""
    sm = _require_session_manager()
    try:
        defn = sm.agent_registry.create_agent(
            template_id=request.template_id,
            id=request.id,
            name=request.name,
            description=request.description,
            tool_overrides=request.tools,
            skill_overrides=request.skills,
            soul_overrides=request.soul,
            model=request.model,
            provider=request.provider,
            mcp_servers=request.mcp_servers,
            color=request.color,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return AgentOut.from_definition(defn)


DEFAULT_TEAM = [
    {"template_id": "researcher", "id": "researcher-1", "name": "Ronny"},
    {"template_id": "developer",  "id": "dev-1",        "name": "David"},
    {"template_id": "tester",     "id": "qa-1",         "name": "Tom"},
    {"template_id": "security",   "id": "sec-1",        "name": "Sally"},
]


@app.post("/agents/create-default-team", dependencies=[Depends(_require_gateway_token)])
async def create_default_team():
    """Create the default team (researcher, developer, tester, security).

    Skips a slot if an agent from that template already exists (by
    template_id, not by id) so it's safe to call repeatedly and
    respects agents the user already created from the same templates."""
    sm = _require_session_manager()
    existing_templates = {
        a.template_id for a in sm.agent_registry.list_agents() if a.template_id
    }
    created = []
    skipped = []
    for spec in DEFAULT_TEAM:
        if spec["template_id"] in existing_templates:
            skipped.append(spec["template_id"])
            continue
        if sm.agent_registry.resolve(spec["id"]) is not None:
            skipped.append(spec["id"])
            continue
        try:
            defn = sm.agent_registry.create_agent(
                template_id=spec["template_id"],
                id=spec["id"],
                name=spec["name"],
            )
            created.append(AgentOut.from_definition(defn))
            existing_templates.add(spec["template_id"])
        except ValueError:
            skipped.append(spec["id"])
    return {"created": created, "skipped": skipped}


@app.get("/agents/{agent_id}", response_model=AgentOut, dependencies=[Depends(_require_gateway_token)])
async def get_agent(agent_id: str):
    sm = _require_session_manager()
    defn = sm.agent_registry.resolve(agent_id)
    if defn is None:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
    return AgentOut.from_definition(defn)


@app.put("/agents/{agent_id}", response_model=AgentOut, dependencies=[Depends(_require_gateway_token)])
async def update_agent(agent_id: str, request: UpdateAgentRequest):
    sm = _require_session_manager()
    try:
        defn = sm.agent_registry.update_agent(
            agent_id,
            name=request.name,
            description=request.description,
            tools=request.tools,
            skills=request.skills,
            soul=request.soul,
            model=request.model,
            provider=request.provider,
            mcp_servers=request.mcp_servers,
            color=request.color,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return AgentOut.from_definition(defn)


@app.delete("/agents/{agent_id}", dependencies=[Depends(_require_gateway_token)])
async def delete_agent(agent_id: str):
    sm = _require_session_manager()
    try:
        existed = sm.agent_registry.delete_agent(agent_id, embeddings=sm.embeddings)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not existed:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
    return {"message": f"Agent '{agent_id}' deleted"}


@app.get("/agent-templates", dependencies=[Depends(_require_gateway_token)])
async def list_agent_templates():
    """Built-in templates (templates/agents/), overridden/extended by any
    user templates of the same id (workspace/agent_templates/)."""
    sm = _require_session_manager()
    return sm.agent_registry.list_templates()


@app.post("/agent-templates", dependencies=[Depends(_require_gateway_token)])
async def save_agent_template(template: Dict[str, Any]):
    """Save a user-defined template - creates a new one, or overrides a
    built-in template of the same id."""
    sm = _require_session_manager()
    try:
        return sm.agent_registry.save_template(template)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/agent-templates/{template_id}", dependencies=[Depends(_require_gateway_token)])
async def delete_agent_template(template_id: str):
    sm = _require_session_manager()
    try:
        existed = sm.agent_registry.delete_template(template_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not existed:
        raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")
    return {"message": f"Template '{template_id}' deleted"}


# --- Provider routes ---

from runtime.providers import ProviderConfig


class ProviderOut(BaseModel):
    id: str
    name: str
    type: str
    base_url: str
    default_model: str
    api_key_env: Optional[str] = None
    extra_headers_env: Optional[str] = None
    is_default: bool = False
    api_key_set: bool = False
    extra_headers_set: bool = False

    @staticmethod
    def from_config(config: ProviderConfig, registry) -> "ProviderOut":
        status = registry.env_var_status(config)
        return ProviderOut(
            id=config.id,
            name=config.name,
            type=config.type,
            base_url=config.base_url,
            default_model=config.default_model,
            api_key_env=config.api_key_env,
            extra_headers_env=config.extra_headers_env,
            is_default=config.is_default,
            api_key_set=status["api_key_set"],
            extra_headers_set=status["extra_headers_set"],
        )


class CreateProviderRequest(BaseModel):
    id: str
    name: str
    type: str
    base_url: str
    default_model: str
    api_key_env: Optional[str] = None
    extra_headers_env: Optional[str] = None
    is_default: bool = False


class UpdateProviderRequest(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    base_url: Optional[str] = None
    default_model: Optional[str] = None
    api_key_env: Optional[str] = None
    extra_headers_env: Optional[str] = None
    is_default: Optional[bool] = None


@app.get("/providers", response_model=List[ProviderOut], dependencies=[Depends(_require_gateway_token)])
async def list_providers():
    sm = _require_session_manager()
    reg = sm.provider_registry
    return [ProviderOut.from_config(p, reg) for p in reg.list_providers()]


@app.post("/providers", response_model=ProviderOut, dependencies=[Depends(_require_gateway_token)])
async def create_provider(request: CreateProviderRequest):
    sm = _require_session_manager()
    reg = sm.provider_registry
    try:
        config = reg.create_provider(
            id=request.id,
            name=request.name,
            type=request.type,
            base_url=request.base_url,
            default_model=request.default_model,
            api_key_env=request.api_key_env,
            extra_headers_env=request.extra_headers_env,
            is_default=request.is_default,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ProviderOut.from_config(config, reg)


@app.get("/providers/{provider_id}", response_model=ProviderOut, dependencies=[Depends(_require_gateway_token)])
async def get_provider(provider_id: str):
    sm = _require_session_manager()
    reg = sm.provider_registry
    config = reg.resolve(provider_id)
    if config is None:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")
    return ProviderOut.from_config(config, reg)


@app.put("/providers/{provider_id}", response_model=ProviderOut, dependencies=[Depends(_require_gateway_token)])
async def update_provider(provider_id: str, request: UpdateProviderRequest):
    sm = _require_session_manager()
    reg = sm.provider_registry
    try:
        config = reg.update_provider(
            provider_id,
            name=request.name,
            type=request.type,
            base_url=request.base_url,
            default_model=request.default_model,
            api_key_env=request.api_key_env,
            extra_headers_env=request.extra_headers_env,
            is_default=request.is_default,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    sm.evict_sessions_for_provider(provider_id)
    return ProviderOut.from_config(config, reg)


@app.delete("/providers/{provider_id}", dependencies=[Depends(_require_gateway_token)])
async def delete_provider(provider_id: str):
    sm = _require_session_manager()
    reg = sm.provider_registry
    existed = reg.delete_provider(provider_id)
    if not existed:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")
    sm.evict_sessions_for_provider(provider_id)
    return {"message": f"Provider '{provider_id}' deleted"}


# --- Per-agent knowledge routes ---

from runtime.knowledge import KnowledgeManager


class KnowledgeFactRequest(BaseModel):
    content: str


class KnowledgeIngestRequest(BaseModel):
    text: str
    source_name: str


class KnowledgeIngestUrlRequest(BaseModel):
    url: str
    source_name: str = ""


class KnowledgeSearchRequest(BaseModel):
    query: str
    n_results: int = 3


def _knowledge_manager(agent_id: str) -> KnowledgeManager:
    sm = _require_session_manager()
    if sm.agent_registry.resolve(agent_id) is None:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
    return KnowledgeManager(
        workspace_dir=str(sm.agent_registry.workspace_dir),
        agent_id=agent_id,
        embeddings=sm.embeddings,
    )


@app.get("/agents/{agent_id}/knowledge", dependencies=[Depends(_require_gateway_token)])
async def list_knowledge_sources(agent_id: str):
    km = _knowledge_manager(agent_id)
    return {"sources": km.list_sources()}


@app.get("/agents/{agent_id}/knowledge/curated", dependencies=[Depends(_require_gateway_token)])
async def get_curated_knowledge(agent_id: str):
    km = _knowledge_manager(agent_id)
    return {"content": km.get_curated_knowledge()}


@app.post("/agents/{agent_id}/knowledge/facts", dependencies=[Depends(_require_gateway_token)])
async def add_knowledge_fact(agent_id: str, request: KnowledgeFactRequest):
    km = _knowledge_manager(agent_id)
    result = km.add_fact(request.content)
    return {"message": result}


@app.post("/agents/{agent_id}/knowledge/documents", dependencies=[Depends(_require_gateway_token)])
async def ingest_knowledge_document(agent_id: str, request: KnowledgeIngestRequest):
    km = _knowledge_manager(agent_id)
    result = km.ingest_text(request.text, request.source_name)
    return {"message": result}


@app.post("/agents/{agent_id}/knowledge/urls", dependencies=[Depends(_require_gateway_token)])
async def ingest_knowledge_url(agent_id: str, request: KnowledgeIngestUrlRequest):
    km = _knowledge_manager(agent_id)
    try:
        result = await asyncio.to_thread(km.ingest_url, request.url, request.source_name)
        return {"message": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/agents/{agent_id}/knowledge/search", dependencies=[Depends(_require_gateway_token)])
async def search_knowledge(agent_id: str, request: KnowledgeSearchRequest):
    km = _knowledge_manager(agent_id)
    results = km.search(request.query, request.n_results)
    return {"results": results}


@app.delete("/agents/{agent_id}/knowledge/{source_name}", dependencies=[Depends(_require_gateway_token)])
async def delete_knowledge_source(agent_id: str, source_name: str):
    km = _knowledge_manager(agent_id)
    result = km.remove_source(source_name)
    return {"message": result}


@app.get("/tools", dependencies=[Depends(_require_gateway_token)])
async def list_tools():
    """Introspect the global tool registry - also what makes the Tools tab
    (and the tool checkboxes when creating/editing an agent) dynamic."""
    from tools.core import TOOLS
    return [
        {"name": t["function"]["name"], "description": t["function"].get("description", "")}
        for t in TOOLS
    ]


@app.get("/skills", dependencies=[Depends(_require_gateway_token)])
async def list_skills_endpoint():
    """The shared skills library (same for every agent's SkillsManager,
    since all agents share one workspace)."""
    sm = _require_session_manager()
    from runtime.skills import SkillsManager
    skills = SkillsManager(str(sm.agent_registry.workspace_dir))
    return list(skills.metadata.values())


class SkillCreateRequest(BaseModel):
    name: str
    description: str
    code: str
    parameters: Optional[Dict[str, str]] = None
    tags: Optional[List[str]] = None
    skill_type: str = "executable"


class SkillUpdateRequest(BaseModel):
    description: Optional[str] = None
    code: Optional[str] = None
    changes: str = ""
    tags: Optional[List[str]] = None
    parameters: Optional[Dict[str, str]] = None


@app.get("/skills/{name}", dependencies=[Depends(_require_gateway_token)])
async def get_skill(name: str):
    """Get a single skill's metadata and source code."""
    sm = _require_session_manager()
    from runtime.skills import SkillsManager
    mgr = SkillsManager(str(sm.agent_registry.workspace_dir))
    meta = mgr.get_skill_info(name)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    ext = ".md" if meta.get("type") == "prompt" else ".py"
    skill_file = mgr.skills_dir / f"{name}{ext}"
    code = skill_file.read_text() if skill_file.exists() else ""
    if meta.get("type") == "prompt" and code:
        parsed = mgr._parse_md_frontmatter(code)
        code = parsed["body"]
    return {**meta, "code": code}


@app.post("/skills", dependencies=[Depends(_require_gateway_token)])
async def create_skill(request: SkillCreateRequest):
    """Create a new skill in the library."""
    sm = _require_session_manager()
    from runtime.skills import SkillsManager
    mgr = SkillsManager(str(sm.agent_registry.workspace_dir))
    result = mgr.create_skill(
        name=request.name,
        description=request.description,
        code=request.code,
        parameters=request.parameters,
        tags=request.tags,
        skill_type=request.skill_type,
    )
    if result.startswith("Error"):
        raise HTTPException(status_code=400, detail=result)
    return mgr.get_skill_info(request.name)


@app.put("/skills/{name}", dependencies=[Depends(_require_gateway_token)])
async def update_skill(name: str, request: SkillUpdateRequest):
    """Update an existing skill's code, description, or tags."""
    sm = _require_session_manager()
    from runtime.skills import SkillsManager
    mgr = SkillsManager(str(sm.agent_registry.workspace_dir))
    meta = mgr.get_skill_info(name)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    if request.description is not None:
        meta["description"] = request.description
        mgr._save_metadata()
    if request.tags is not None:
        meta["tags"] = request.tags
        mgr._save_metadata()
    if request.parameters is not None:
        meta["parameters"] = request.parameters
        mgr._save_metadata()
    if request.code is not None:
        result = mgr.improve_skill(name, request.changes or "Updated via UI", request.code)
        if result.startswith("Error"):
            raise HTTPException(status_code=400, detail=result)
    return mgr.get_skill_info(name)


@app.delete("/skills/{name}", dependencies=[Depends(_require_gateway_token)])
async def delete_skill(name: str):
    """Delete a skill from the library."""
    sm = _require_session_manager()
    from runtime.skills import SkillsManager
    mgr = SkillsManager(str(sm.agent_registry.workspace_dir))
    result = mgr.delete_skill(name)
    if result.startswith("Error"):
        raise HTTPException(status_code=404, detail=result)
    return {"message": result}


async def _generate_broadcast_reactions(message: str, message_id: str, session_id: str):
    """Ask the LLM to pick one emoji per team member, then broadcast them."""
    try:
        agents = session_manager.agent_registry.list_agents()
        if not agents:
            return

        agent_lines = "\n".join(
            f"- {a.id}: {a.name} ({a.description or 'team member'})"
            for a in agents
        )

        prompt = (
            f"A team member just posted this in the team chat:\n"
            f'"{message}"\n\n'
            f"Team members:\n{agent_lines}\n\n"
            f"Pick ONE emoji reaction for each team member that fits their role/personality "
            f"and the message content. Respond with ONLY a JSON object mapping agent id to a single emoji.\n"
            f'Example: {{"kit": "\U0001f44d", "dev-1": "\U0001f525"}}'
        )

        kit_defn = session_manager.agent_registry.resolve("kit")
        client, model, _ = session_manager.create_client_for_agent(kit_defn)

        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            stream=False,
        )

        text = response.choices[0].message.content.strip()
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        code_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL)
        if code_match:
            text = code_match.group(1)

        reactions_map = json.loads(text)

        reactions = []
        for agent in agents:
            emoji = reactions_map.get(agent.id)
            if emoji:
                reactions.append({
                    "agent_id": agent.id,
                    "agent_name": agent.name,
                    "emoji": emoji,
                })

        if reactions:
            await manager.broadcast({
                "type": "broadcast_reactions",
                "message_id": message_id,
                "reactions": reactions,
                "timestamp": _now(),
            })
            session_manager.save_message(
                session_id, "reactions", "", message_id=message_id, reactions=reactions
            )
    except Exception as e:
        print(f"Warning: Failed to generate broadcast reactions: {e}")


async def _generate_broadcast_replies(message: str, session_id: str):
    """Have every agent reply to a broadcast message (no tools, just chat)."""
    try:
        agents = session_manager.agent_registry.list_agents()
        if not agents:
            return

        async def _reply(agent_defn):
            try:
                soul = agent_defn.soul or "You are a helpful team member."
                system = (
                    f"Your name is {agent_defn.name}. "
                    f"Your role on the team: {agent_defn.description}\n\n"
                    f"{soul}\n\n"
                    "You are replying in the team chat. Rules:\n"
                    "- Keep it to 1-2 sentences.\n"
                    "- Respond from YOUR role's perspective only. A developer "
                    "talks about code/technical concerns. A researcher talks "
                    "about information/findings. A manager coordinates and "
                    "delegates. A comedian cracks jokes. Do NOT give generic "
                    "helpful-assistant answers.\n"
                    "- Do NOT manage, delegate, or organize unless your role "
                    "is specifically a manager."
                )
                client, model, _ = session_manager.create_client_for_agent(agent_defn)

                response = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": message},
                    ],
                    stream=False,
                )

                text = response.choices[0].message.content.strip()
                text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
                if not text:
                    return

                session_manager.save_message(
                    session_id, "assistant", text, agent_id=agent_defn.id
                )
                await manager.broadcast({
                    "type": "broadcast_reply",
                    "session_id": session_id,
                    "agent_id": agent_defn.id,
                    "agent_name": agent_defn.name,
                    "message": text,
                    "timestamp": _now(),
                })
            except Exception as e:
                print(f"Warning: Agent {agent_defn.id} failed to reply to broadcast: {e}")

        order = list(agents)
        random.shuffle(order)
        for agent_defn in order:
            await asyncio.sleep(random.uniform(1.0, 3.0))
            await _reply(agent_defn)
    except Exception as e:
        print(f"Warning: Failed to generate broadcast replies: {e}")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time updates.

    Protocol:
    - Client connects
    - Server sends events: chat_message, session_update, tool_execution
    - Client can send: ping, subscribe

    Requires GATEWAY_TOKEN auth when GATEWAY_TOKEN is set, via either the
    `kit-token.<base64url token>` WebSocket subprotocol (preferred - see
    _websocket_auth) or a `?token=...` query param (legacy fallback).
    """
    is_valid, matched_subprotocol = _websocket_auth(websocket)
    if not is_valid:
        await websocket.close(code=1008)
        return

    await manager.connect(websocket, subprotocol=matched_subprotocol)

    try:
        # Send welcome message
        await websocket.send_json({
            "type": "connected",
            "message": "Connected to Kit Gateway",
            "timestamp": _now()
        })

        # Listen for messages
        while True:
            data = await websocket.receive_text()

            try:
                message = json.loads(data)
                msg_type = message.get("type")

                if msg_type == "ping":
                    await websocket.send_json({
                        "type": "pong",
                        "timestamp": _now()
                    })

                elif msg_type == "subscribe":
                    await websocket.send_json({
                        "type": "subscribed",
                        "message": "Subscribed to all events"
                    })

                elif msg_type == "chat_message":
                    platform = message.get("platform", "web")
                    user_id = message.get("user_id", "anonymous")
                    agent_id = message.get("agent_id", "kit")
                    user_msg = message.get("message", "")
                    user_msg_id = message.get("message_id", str(uuid.uuid4()))
                    session_id = make_session_id(platform, user_id, agent_id)

                    if not check_rate_limit(f"{platform}:{user_id}"):
                        await websocket.send_json({
                            "type": "stream_error",
                            "session_id": session_id,
                            "error": "Rate limit exceeded, try again shortly",
                            "timestamp": _now(),
                        })
                        continue

                    session_manager.save_message(session_id, "user", user_msg, message_id=user_msg_id)

                    await manager.broadcast({
                        "type": "user_message",
                        "session_id": session_id,
                        "platform": platform,
                        "agent_id": agent_id,
                        "message": user_msg,
                        "message_id": user_msg_id,
                        "timestamp": _now(),
                    })

                    try:
                        async for event in session_manager.send_message_stream(
                            platform=platform, user_id=user_id, message=user_msg, agent_id=agent_id
                        ):
                            await websocket.send_json({
                                **event,
                                "session_id": session_id,
                                "timestamp": _now(),
                            })

                            if event["type"] == "stream_end":
                                assistant_content = event.get("content", "")
                                assistant_msg_id = str(uuid.uuid4())
                                session_manager.save_message(session_id, "assistant", assistant_content, message_id=assistant_msg_id)
                                stats = session_manager.get_session_stats(session_id)
                                await manager.broadcast({
                                    "type": "assistant_message",
                                    "session_id": session_id,
                                    "platform": platform,
                                    "agent_id": agent_id,
                                    "message": assistant_content,
                                    "message_count": stats["message_count"] if stats else 0,
                                    "timestamp": _now(),
                                })
                    except Exception as e:
                        await websocket.send_json({
                            "type": "stream_error",
                            "session_id": session_id,
                            "error": str(e),
                            "timestamp": _now(),
                        })

                elif msg_type == "add_reaction":
                    msg_id = message.get("message_id", "")
                    session_id = message.get("session_id", "")
                    emoji = message.get("emoji", "")
                    user_id = message.get("user_id", "anonymous")
                    # Require the reaction to reference a message that
                    # actually exists in that session, rather than trusting
                    # a client-supplied session_id outright - stops a client
                    # from injecting reactions into a session_id it merely
                    # guessed rather than one it has actually seen traffic
                    # for.
                    target_exists = msg_id and session_id and any(
                        m.get("message_id") == msg_id
                        for m in session_manager.get_messages(session_id)
                    )
                    if msg_id and emoji and session_id and target_exists:
                        session_manager.save_message(
                            session_id, "user_reactions", "",
                            message_id=msg_id, emoji=emoji, user_id=user_id,
                        )
                        await manager.broadcast({
                            "type": "message_reaction",
                            "message_id": msg_id,
                            "session_id": session_id,
                            "emoji": emoji,
                            "user_id": user_id,
                            "timestamp": _now(),
                        })

                elif msg_type == "broadcast":
                    platform = message.get("platform", "web")
                    user_id = message.get("user_id", "anonymous")
                    user_msg = message.get("message", "")
                    msg_id = message.get("message_id", str(uuid.uuid4()))
                    session_id = _broadcast_session_id(platform, user_id)

                    if not check_rate_limit(f"broadcast:{platform}:{user_id}"):
                        await websocket.send_json({
                            "type": "error",
                            "message": "Rate limit exceeded, try again shortly",
                        })
                        continue

                    mem = MemoryManager(str(session_manager.agent_registry.workspace_dir))
                    mem.save_broadcast(user_msg)
                    session_manager.save_message(session_id, "user", user_msg, message_id=msg_id)

                    await manager.broadcast({
                        "type": "user_message",
                        "session_id": session_id,
                        "platform": platform,
                        "message": user_msg,
                        "message_id": msg_id,
                        "timestamp": _now(),
                    })

                    asyncio.create_task(_generate_broadcast_reactions(user_msg, msg_id, session_id))
                    asyncio.create_task(_generate_broadcast_replies(user_msg, session_id))

            except json.JSONDecodeError:
                await websocket.send_json({
                    "type": "error",
                    "message": "Invalid JSON"
                })

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        print(f"WebSocket error: {e}")
        manager.disconnect(websocket)


def start_server(host: str = "127.0.0.1", port: int = 18789):
    """
    Start the gateway server.

    Args:
        host: Host to bind to
        port: Port to listen on
    """
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    start_server(
        host=os.environ.get("GATEWAY_HOST", "127.0.0.1"),
        port=env_int("GATEWAY_PORT", 18789),
    )
