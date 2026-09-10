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
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any, Optional, Set, Union, Literal
from pathlib import Path
from datetime import datetime, timezone
import uvicorn
import json
import asyncio
import secrets

import os
from env_config import env_int
from gateway.session_manager import SessionManager, make_session_id
from gateway.scheduler import run_scheduler


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


def _websocket_token_valid(websocket: WebSocket) -> bool:
    """Same check as _require_gateway_token, adapted for the WebSocket
    handshake (browsers can't set custom headers on a WS connection, so the
    token travels as a query param instead: `/ws?token=...`)."""
    token = os.environ.get("GATEWAY_TOKEN")
    if not token:
        return True

    provided = websocket.query_params.get("token")
    return bool(provided) and secrets.compare_digest(provided, token)


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

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
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
    session_manager = SessionManager(
        llm_base_url=base_url,
        model=model,
        llm_provider=provider,
        llm_api_key=api_key,
        llm_extra_headers=extra_headers,
        on_event=manager.broadcast
    )
    scheduler_task = asyncio.create_task(run_scheduler(session_manager, manager))
    print("✅ Gateway server started")
    print(f"🤖 LLM: {model} at {base_url} (provider={provider})")
    print("⏰ Scheduler running (60s check interval)")
    print("📡 Ready to handle multi-platform requests")

    yield

    scheduler_task.cancel()
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

    @staticmethod
    def from_definition(defn) -> "AgentOut":
        return AgentOut(
            id=defn.id, name=defn.name, description=defn.description,
            template_id=defn.template_id, tools=defn.tools, skills=defn.skills,
            model=defn.model, provider=defn.provider, soul=defn.soul,
        )


class CreateAgentRequest(BaseModel):
    template_id: str
    id: str
    name: Optional[str] = None
    description: Optional[str] = None
    tools: Optional[Union[List[str], Literal["*"]]] = None
    skills: Optional[Union[List[str], Literal["*"]]] = None
    soul: Optional[str] = None


class UpdateAgentRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    tools: Optional[Union[List[str], Literal["*"]]] = None
    skills: Optional[Union[List[str], Literal["*"]]] = None
    soul: Optional[str] = None


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
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return AgentOut.from_definition(defn)


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
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return AgentOut.from_definition(defn)


@app.delete("/agents/{agent_id}", dependencies=[Depends(_require_gateway_token)])
async def delete_agent(agent_id: str):
    sm = _require_session_manager()
    try:
        existed = sm.agent_registry.delete_agent(agent_id)
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


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time updates.

    Protocol:
    - Client connects
    - Server sends events: chat_message, session_update, tool_execution
    - Client can send: ping, subscribe

    Requires ?token=<GATEWAY_TOKEN> in the connection URL when GATEWAY_TOKEN
    is set (browsers can't attach an Authorization header to a WebSocket
    handshake, so the token travels as a query param here instead).
    """
    if not _websocket_token_valid(websocket):
        await websocket.close(code=1008)
        return

    await manager.connect(websocket)

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
                    session_id = make_session_id(platform, user_id, agent_id)

                    session_manager.save_message(session_id, "user", user_msg)

                    await manager.broadcast({
                        "type": "user_message",
                        "session_id": session_id,
                        "platform": platform,
                        "agent_id": agent_id,
                        "message": user_msg,
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
                                session_manager.save_message(session_id, "assistant", assistant_content)
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
