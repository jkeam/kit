"""
Gateway Server - FastAPI server for multi-platform message routing.

Handles:
- HTTP API for channel communication
- Session management
- Message routing to agent runtime
- Event broadcasting (future: WebSockets)
"""

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any, Optional, Set
from pathlib import Path
import uvicorn
import json
import asyncio

from gateway.session_manager import SessionManager


# Request/Response models
class ChatRequest(BaseModel):
    """Request format for chat endpoint."""
    platform: str
    user_id: str
    message: str
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
    created_at: str
    last_active: str
    message_count: int


# Create FastAPI app
app = FastAPI(
    title="Kit Gateway",
    description="Gateway for Kit - Your AI Toolkit",
    version="0.1.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files for web UI
web_dir = Path(__file__).parent.parent / "web"
if web_dir.exists():
    app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")

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


@app.on_event("startup")
async def startup_event():
    """Initialize gateway on startup."""
    global session_manager
    session_manager = SessionManager()
    print("✅ Gateway server started")
    print("📡 Ready to handle multi-platform requests")


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown."""
    print("🛑 Gateway server shutting down")


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


@app.post("/chat", response_model=ChatResponse)
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
        session_id = f"{request.platform}:{request.user_id}"

        # Broadcast user message event
        await manager.broadcast({
            "type": "user_message",
            "session_id": session_id,
            "platform": request.platform,
            "message": request.message,
            "timestamp": asyncio.get_event_loop().time()
        })

        # Send message through session manager
        response = session_manager.send_message(
            platform=request.platform,
            user_id=request.user_id,
            message=request.message
        )

        # Get session stats
        stats = session_manager.get_session_stats(session_id)

        # Broadcast assistant response event
        await manager.broadcast({
            "type": "assistant_message",
            "session_id": session_id,
            "platform": request.platform,
            "message": response,
            "message_count": stats["message_count"] if stats else 0,
            "timestamp": asyncio.get_event_loop().time()
        })

        return ChatResponse(
            response=response,
            session_id=session_id,
            message_count=stats["message_count"] if stats else 0
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing message: {str(e)}")


@app.get("/sessions", response_model=List[SessionStats])
async def list_sessions():
    """List all active sessions."""
    if not session_manager:
        raise HTTPException(status_code=500, detail="Session manager not initialized")

    sessions = session_manager.list_sessions()
    return [
        SessionStats(
            session_id=s.session_id,
            platform=s.platform,
            user_id=s.user_id,
            created_at=s.created_at.isoformat(),
            last_active=s.last_active.isoformat(),
            message_count=s.message_count
        )
        for s in sessions
    ]


@app.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """Get specific session details."""
    if not session_manager:
        raise HTTPException(status_code=500, detail="Session manager not initialized")

    stats = session_manager.get_session_stats(session_id)
    if not stats:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    return stats


@app.delete("/sessions/{session_id}")
async def clear_session(session_id: str):
    """Clear a specific session."""
    if not session_manager:
        raise HTTPException(status_code=500, detail="Session manager not initialized")

    success = session_manager.clear_session(session_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    return {"message": f"Session {session_id} cleared"}


@app.post("/sessions/cleanup")
async def cleanup_sessions(max_age_minutes: int = 60):
    """Cleanup inactive sessions."""
    if not session_manager:
        raise HTTPException(status_code=500, detail="Session manager not initialized")

    removed = session_manager.cleanup_inactive_sessions(max_age_minutes)
    return {
        "message": f"Cleaned up {removed} inactive sessions",
        "removed_count": removed
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time updates.

    Protocol:
    - Client connects
    - Server sends events: chat_message, session_update, tool_execution
    - Client can send: ping, subscribe
    """
    await manager.connect(websocket)

    try:
        # Send welcome message
        await websocket.send_json({
            "type": "connected",
            "message": "Connected to Kit Gateway",
            "timestamp": asyncio.get_event_loop().time()
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
                        "timestamp": asyncio.get_event_loop().time()
                    })

                elif msg_type == "subscribe":
                    # Subscribe to events (future: filter by session_id)
                    await websocket.send_json({
                        "type": "subscribed",
                        "message": "Subscribed to all events"
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
    start_server()
