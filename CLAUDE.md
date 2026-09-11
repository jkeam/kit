# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Kit is a production-ready personal AI assistant built with LlamaStack (soon OGX), featuring:
- Gateway-Workspace-Agent architecture (inspired by OpenClaw)
- 3-layer memory system (long-term MEMORY.md, daily logs, vector embeddings)
- 17 tools across file ops, memory, web, browser, and scheduling
- Real-time WebSocket-based PatternFly UI
- Multi-session management with platform:user_id isolation

**Tech Stack**: Python 3.14+, FastAPI, LlamaStack Client, ChromaDB (embeddings), Playwright (browser automation), PatternFly v5 (UI)

## Development Commands

### Running the Application

**Start Gateway (main entry point)**
```bash
# Using uv (recommended - automatically uses venv)
uv run python -m gateway.server

# OR activate venv first, then run
source .venv/bin/activate
python -m gateway.server

# Starts on http://127.0.0.1:18789
# Serves web UI and handles WebSocket connections
```

**Start an LLM provider** (pick one)
```bash
# Option A: Ollama
ollama pull qwen3:14b && ollama serve
# Set LLM_PROVIDER=ollama, LLM_BASE_URL=http://localhost:11434/v1, LLM_MODEL=qwen3:14b in .env

# Option B: LlamaStack / OGX
uv run llama stack run llama-stack-run.yaml
# Starts on http://127.0.0.1:8321, uses defaults in .env (LLM_PROVIDER=llamastack)

# Option C: OpenCode Zen (https://opencode.ai/zen)
# Set LLM_PROVIDER=openai, LLM_BASE_URL=https://opencode.ai/zen/v1,
# LLM_MODEL=<model id>, LLM_API_KEY=<your OpenCode Zen key> in .env
```

**CLI Usage**
```bash
uv run python cli.py "What files are in this directory?"
uv run python cli.py --stats  # Show session statistics

# OR with activated venv
source .venv/bin/activate
python cli.py "What files are in this directory?"
```

### Setup

**Install dependencies (using uv)**
```bash
uv venv
source .venv/bin/activate
uv sync  # Installs from pyproject.toml
playwright install chromium  # Required for browser tools
```

**OR using pip directly**
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .  # Installs from pyproject.toml
playwright install chromium
```

**Environment configuration**
```bash
cp .env.example .env
# Add TAVILY_API_KEY for web search
# GATEWAY_TOKEN is optional
```

## Architecture

### Request Flow
```
User → Web UI/CLI → Gateway (FastAPI) → PersonalAssistant → LlamaStack → RedHat MaaS (Qwen3-14B)
                         ↓
                    SessionManager (isolates by platform:user_id)
                         ↓
                    Runtime (Memory + Tools + Custom Tools & Skills)
```

### Core Components

**gateway/** - FastAPI server with WebSocket broadcasting
- `server.py`: Main server, routes, WebSocket manager
- `session_manager.py`: Per-session PersonalAssistant instances
- Sessions identified by `{platform}:{user_id}` (e.g., "web:anonymous")

**runtime/** - Agent orchestration and memory
- `agent.py`: `PersonalAssistant` class - main orchestration logic
  - Builds system prompt from SOUL.md + AGENTS.md + memory context
  - Handles ReAct tool-use loop via LlamaStack
  - Coordinates memory, embeddings, custom tools, and skills
- `memory.py`: `MemoryManager` - 3-layer memory system
  - Long-term: `workspace/MEMORY.md` (curated facts)
  - Daily logs: `workspace/memory/YYYY-MM-DD.md` (conversation history)
  - Loads today + yesterday logs into context automatically
- `embeddings.py`: `EmbeddingsManager` - vector search with ChromaDB
  - Uses SentenceTransformer (all-MiniLM-L6-v2)
  - Indexes workspace/ directory recursively
  - Provides semantic_search(query, k=5) for memory retrieval
- `skills.py`: `SkillsManager` - manages custom tools and skills
  - Custom tools (`.py`): sandboxed Python scripts executed via subprocess
  - Skills (`.md`): markdown domain guides injected into the system prompt (NVIDIA SKILL.md format compatible)
  - Both are auto-discovered from `workspace/skills/`

**tools/** - 17 built-in tools
- `core.py`: File ops (read, write, list_files), shell (exec_shell), memory (memory_write, memory_get, memory_search)
- `web.py`: web_search (Tavily), web_fetch (httpx)
- `browser.py`: Playwright automation (browser_screenshot, browser_navigate, browser_extract)
  - Runs in subprocess for isolation
  - Uses chromium in non-headless mode
- `scheduler.py`: schedule_create, schedule_list, schedule_delete
  - Stores schedules as JSON in `workspace/schedules/`
  - Background cron runner checks every 60s and sends due tasks to the assistant via isolated sessions
- `skills.py`: skill_list, skill_execute, skill_create (custom tools & skills management)

**workspace/** - Memory and user data
- `SOUL.md`: Core personality and behavior (loaded into system prompt)
- `AGENTS.md`: Agent-specific instructions (loaded into system prompt)
- `MEMORY.md`: Long-term curated memory (manually edited or via memory_write tool)
- `USER.md`: User preferences and context
- `memory/`: Daily logs (auto-generated: YYYY-MM-DD.md)
- `schedules/`: Schedule JSON files
- `skills/`: Custom tools (`.py`) and skills (`.md`), dynamically loaded

**web/** - PatternFly-based frontend
- `index.html`: Main UI with tabs (Chat, Sessions, Memory, Tools, Schedules, Custom Tools & Skills)
- `app.js`: WebSocket client, real-time updates, multi-tab broadcasting
- `styles.css`: PatternFly v5 theming
- Served by gateway at `/` and `/static/`

### Key Patterns

**System Prompt Construction** (`runtime/agent.py:_build_system_prompt()`)
1. Load SOUL.md (personality)
2. Append AGENTS.md (behavior)
3. Append memory context (MEMORY.md + today's log + yesterday's log)
4. Result: Combined system prompt with all context

**Memory Loading** (`runtime/memory.py:load_context()`)
- Always includes: MEMORY.md, today's log, yesterday's log
- Interactions auto-logged via `log_interaction(user_msg, assistant_response)`
- Old logs cleaned up after 7 days (configurable via retention_days)

**Tool Execution Flow**
1. LlamaStack calls tool via OpenAI tool-use format
2. `runtime/agent.py:chat()` receives tool_call from client
3. Dispatches to `tools/core.py:execute_tool(name, args)`
4. Result returned to LlamaStack, continues ReAct loop
5. Final response sent to user

**WebSocket Broadcasting** (`gateway/server.py:ConnectionManager`)
- All connected clients receive updates in real-time
- Used for: chat messages, session updates, tool executions
- Auto-reconnects on disconnect (2-10s backoff)

**Custom Tools & Skills** (`runtime/skills.py` + `tools/skills.py`)
- Custom tools (`.py`): sandboxed Python scripts, executed via subprocess on demand
- Skills (`.md`): markdown domain guides with YAML frontmatter, injected into system prompt automatically
- Both types live in `workspace/skills/` and are auto-discovered on startup
- Example custom tool: `workspace/skills/count-python-files.py`

## Configuration Files

**config.yaml** - Tool safety settings only (`tools.safety.shell_confirm_destructive`, `allowed_commands`). Gateway host/port/auth, LLM provider/model, and memory retention are all env-var-driven (see `.env.example`) rather than living here — see "LLM provider selection" below and `.env.example` for the full list.

**llama-stack-run.yaml** - LlamaStack server config
- Provider: RedHat MaaS (OpenAI-compatible)
- Model: qwen3-14b
- Base URL: https://maas-rhdp.apps.maas.redhatworkshops.io/v1
- **Note**: API key is hardcoded (not secure for production)

**LLM provider selection** (`runtime/agent.py`, `gateway/server.py`)
- `LLM_PROVIDER` env var chooses the client: `llamastack` (default, uses `LlamaStackClient`) or an OpenAI-compatible provider (`ollama`, `openai`), which uses the standard `openai` client instead
- `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` are passed through to whichever client is selected; OpenAI-compatible providers need a `/v1`-suffixed base URL (e.g. OpenCode Zen: `https://opencode.ai/zen/v1`)
- `LLM_EXTRA_HEADERS` (optional): JSON object of extra HTTP headers sent with every LLM request, for either client (e.g. `{"x-opencode-session": "..."}` for OpenCode Zen)

**pyproject.toml** - Python dependencies
- Core: llama-stack, llama-stack-client, ramalama
- Web: fastapi, uvicorn, httpx, aiohttp
- Data: chromadb-client, faiss-cpu
- Automation: playwright
- Utilities: pydantic, python-dotenv, rich, click

## Important Implementation Details

### Memory System (3 layers)
1. **Long-term** (MEMORY.md): Manually curated, loaded into every prompt
2. **Daily logs** (memory/YYYY-MM-DD.md): Auto-logged conversations, today + yesterday loaded
3. **Vector embeddings** (ChromaDB): Semantic search via `memory_search` tool

When adding memory features:
- Write to MEMORY.md for permanent facts (via memory_write tool)
- Daily logs are automatic (don't modify directly)
- Embeddings index workspace/ recursively (re-index via `embeddings.index_workspace()`)

### Tool Security
- Shell commands: `sudo` blocked, 30s timeout
- File operations: Full filesystem access (no sandboxing)
- Browser automation: Runs in subprocess for isolation
- **Authentication is opt-in**: unset `GATEWAY_TOKEN` (the default) leaves the gateway open for local/trusted use; setting it requires a matching bearer token on every API route and the `/ws` WebSocket (see `gateway/server.py:_require_gateway_token`)

### Session Isolation
- Sessions keyed by `{platform}:{user_id}` (e.g., "web:anonymous", "cli:jkeam")
- Each session gets its own PersonalAssistant instance
- Memory is shared across sessions (same workspace/)
- WebSocket broadcasts to all clients regardless of session

### Browser Tools Subprocess Pattern
Browser tools run in separate process:
```python
# tools/browser.py
result = subprocess.run(
    ["python", "-c", script],
    capture_output=True,
    timeout=60
)
```
This isolates Playwright from FastAPI's event loop (avoids asyncio conflicts)

### Custom Tools & Skills

**Custom tools** (`.py`) are sandboxed Python scripts:
- Define a `main(**kwargs)` function that returns a string result
- Execution: subprocess with 60s timeout, restricted imports
- Discovery: automatic via SkillsManager on init

Example custom tool:
```python
def main(**kwargs):
    text = kwargs.get("text", "")
    return f"{len(text.split())} words"
```

**Skills** (`.md`) are markdown domain guides:
- Use YAML frontmatter for name, description, and tags (NVIDIA SKILL.md format)
- Injected into the agent's system prompt automatically
- Not executable — they shape how the agent thinks, not what it runs

Example skill:
```markdown
---
name: python-best-practices
description: Python coding standards
metadata:
  tags:
    - python
---

# Python Best Practices
...
```

## Known Limitations

- **Schedules**: Runs in isolated sessions; no failure alerting yet
- **Web search**: Requires Tavily API key
- **Browser automation**: Basic actions only (screenshot, navigate, extract)
- **No persistence**: WebSocket sessions are memory-only
- **Synchronous processing**: One message at a time per session
- **Authentication is opt-in**: set `GATEWAY_TOKEN` to require a bearer token; unset by default (local use only), and tools still have full filesystem access regardless

## Testing the Application

**Quick smoke test**
```bash
# Terminal 1: Start gateway
uv run python -m gateway.server

# Terminal 2: Send a message
uv run python cli.py "List files in workspace/"

# Or open browser
open http://localhost:18789
```

**Test WebSocket broadcasting**
1. Open http://localhost:18789 in two tabs
2. Send message in tab 1
3. Verify it appears in both tabs

**Test tools**
```bash
uv run python cli.py "Create a file called test.txt with content 'Hello'"
uv run python cli.py "Read test.txt"
uv run python cli.py "Take a screenshot of https://github.com"  # Requires playwright
uv run python cli.py "Search the web for Python 3.14 features"  # Requires TAVILY_API_KEY
```

## Common Development Tasks

**Add a new tool**
1. Define function in appropriate tools/*.py file
2. Add OpenAI-format tool definition to TOOLS list
3. Add to TOOL_FUNCTIONS mapping
4. Restart gateway (tools loaded on init)

**Modify personality**
- Edit `workspace/SOUL.md`
- Changes take effect on next message (system prompt rebuilt per-message)

**Extend memory system**
- Long-term facts: Add to `workspace/MEMORY.md` or use memory_write tool
- Query memory: Use memory_search tool (semantic) or memory_get (full context)
- Vector search: `embeddings.semantic_search(query, k=5)` in runtime/embeddings.py

**Debug WebSocket issues**
- Check browser console for connection status
- Gateway logs WebSocket connects/disconnects
- ConnectionManager tracks active_connections set

## When Working With This Codebase

- **System prompt**: Rebuilt on every chat() call from SOUL.md + AGENTS.md + memory
- **Tool execution**: Synchronous, runs in same process (except browser tools)
- **Memory loading**: Automatic - includes today + yesterday logs always
- **WebSocket**: Broadcasts to all clients, no session filtering
- **Port conflicts**: Gateway on 18789, LlamaStack on 8321
- **Dependencies**: Playwright browsers must be installed separately
