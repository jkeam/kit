# Kit Architecture Guide

This document describes Kit's architecture: how the system is organized, how data flows through it, and how the major subsystems interact.

---

## Table of Contents

1. [System Overview](#system-overview)
2. [High-Level Architecture](#high-level-architecture)
3. [Request Flow](#request-flow)
4. [Component Deep Dives](#component-deep-dives)
   - [Gateway Layer](#gateway-layer)
   - [Session Management](#session-management)
   - [Agent Runtime](#agent-runtime)
   - [Memory System](#memory-system)
   - [Knowledge System](#knowledge-system)
   - [Tool System](#tool-system)
   - [Skills System](#skills-system)
   - [Provider Registry](#provider-registry)
   - [MCP Integration](#mcp-integration)
   - [Background Services](#background-services)
   - [Web Frontend](#web-frontend)
5. [Multi-Agent Architecture](#multi-agent-architecture)
6. [Data Storage Layout](#data-storage-layout)
7. [Security Model](#security-model)

---

## System Overview

Kit is a multi-agent AI assistant with a Gateway-Runtime architecture. A FastAPI gateway handles HTTP/WebSocket traffic and manages sessions, while the runtime layer orchestrates LLM interactions, tool execution, and a multi-layer memory system. Multiple agents form a team, each with their own persona, tools, knowledge, and memory, coordinated through a delegation protocol.

```
                          Kit System Overview

  ┌─────────────────────────────────────────────────────────────────┐
  │                         CLIENTS                                 │
  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
  │  │ Web UI   │  │   CLI    │  │ WebSocket│  │ HTTP API │       │
  │  │(PatternFly│  │ (cli.py) │  │ Clients  │  │ Clients  │       │
  │  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘       │
  └───────┼──────────────┼────────────┼──────────────┼─────────────┘
          │              │            │              │
          ▼              ▼            ▼              ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │                    GATEWAY (FastAPI)                             │
  │  ┌──────────┐  ┌──────────────┐  ┌────────────┐  ┌──────────┐ │
  │  │  Routes  │  │  WebSocket   │  │   Session   │  │  Auth    │ │
  │  │ /chat    │  │  Manager     │  │   Manager   │  │ (Bearer) │ │
  │  │ /agents  │  │  (broadcast) │  │             │  │          │ │
  │  │ /stream  │  │              │  │             │  │          │ │
  │  └────┬─────┘  └──────┬───────┘  └──────┬──────┘  └──────────┘ │
  │       │               │                 │                       │
  │  ┌────┴─────────────────────────────────┘                       │
  │  │  Background Services                                         │
  │  │  ┌────────────┐  ┌────────────┐                              │
  │  │  │ Scheduler  │  │  Dreamer   │                              │
  │  │  │ (cron)     │  │ (reflect)  │                              │
  │  │  └────────────┘  └────────────┘                              │
  └──┼──────────────────────────────────────────────────────────────┘
     │
     ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │                    RUNTIME                                      │
  │                                                                 │
  │  ┌──────────────────────────────────┐  ┌─────────────────────┐ │
  │  │      PersonalAssistant           │  │   Agent Registry    │ │
  │  │  ┌────────┐  ┌───────────────┐   │  │  ┌──────────────┐  │ │
  │  │  │ System │  │  ReAct Loop   │   │  │  │  Templates   │  │ │
  │  │  │ Prompt │  │  (tool use)   │   │  │  │  Definitions │  │ │
  │  │  │ Build  │  │               │   │  │  │  CRUD        │  │ │
  │  │  └────────┘  └───────┬───────┘   │  │  └──────────────┘  │ │
  │  │                      │           │  └─────────────────────┘ │
  │  │  ┌───────┐  ┌────────┴────────┐  │                          │
  │  │  │Memory │  │  Tool Dispatch  │  │  ┌─────────────────────┐ │
  │  │  │Manager│  │                 │  │  │ Provider Registry   │ │
  │  │  └───────┘  └─────────────────┘  │  │  (LLM configs)     │ │
  │  │                                  │  └─────────────────────┘ │
  │  │  ┌───────┐  ┌────────┐          │                           │
  │  │  │Knowl. │  │ Skills │          │  ┌─────────────────────┐ │
  │  │  │Manager│  │Manager │          │  │   Embeddings        │ │
  │  │  └───────┘  └────────┘          │  │   (ChromaDB)        │ │
  │  │                                  │  └─────────────────────┘ │
  │  │  ┌───────┐                       │                          │
  │  │  │  MCP  │                       │                          │
  │  │  │Client │                       │                          │
  │  │  └───────┘                       │                          │
  │  └──────────────────────────────────┘                          │
  └─────────────────────────────────────────────────────────────────┘
     │
     ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │                    TOOLS                                        │
  │  ┌────────┐ ┌─────┐ ┌─────────┐ ┌──────────┐ ┌─────────────┐ │
  │  │  Core  │ │ Web │ │ Browser │ │Scheduler │ │ Delegation  │ │
  │  │read    │ │fetch│ │navigate │ │create    │ │agent_delegate│ │
  │  │write   │ │srch │ │extract  │ │list      │ │             │ │
  │  │list    │ │     │ │screensht│ │delete    │ │             │ │
  │  │shell   │ │     │ │         │ │          │ │             │ │
  │  │memory  │ │     │ │         │ │          │ │             │ │
  │  └────────┘ └─────┘ └─────────┘ └──────────┘ └─────────────┘ │
  │  ┌─────────────┐ ┌──────────────┐                              │
  │  │   Skills    │ │  Knowledge   │                              │
  │  │create/exec  │ │search/teach  │                              │
  │  │improve/list │ │ingest/forget │                              │
  │  └─────────────┘ └──────────────┘                              │
  └─────────────────────────────────────────────────────────────────┘
     │
     ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │                    LLM PROVIDERS                                │
  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐         │
  │  │  LlamaStack  │  │    Ollama    │  │   OpenAI-    │         │
  │  │  / OGX       │  │              │  │  compatible  │         │
  │  └──────────────┘  └──────────────┘  └──────────────┘         │
  └─────────────────────────────────────────────────────────────────┘
```

---

## Request Flow

### Chat Message (Streaming)

```
User types message in Web UI
         │
         ▼
┌─────────────────────────┐
│  WebSocket sends JSON   │
│  { type: "chat_message",│
│    message: "...",      │
│    agent_id: "kit" }    │
└────────┬────────────────┘
         │
         ▼
┌─────────────────────────┐
│  gateway/server.py      │
│  websocket_endpoint()   │
│  1. Auth check          │
│  2. Rate limit check    │
│  3. Save user msg       │
│  4. Broadcast user_msg  │
└────────┬────────────────┘
         │
         ▼
┌─────────────────────────┐
│  SessionManager         │
│  send_message_stream()  │
│  1. get_session()       │  ──► Creates PersonalAssistant
│  2. _run_and_track()    │      if session is new
│     - set status: busy  │
│     - yield events      │
│     - set status: idle  │
└────────┬────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────┐
│  PersonalAssistant.chat_stream()                │
│                                                  │
│  1. Connect MCP servers (if configured)          │
│  2. Resolve context window limit                 │
│  3. Build system prompt:                         │
│     ┌──────────────────────────────────────┐     │
│     │ SOUL.md  (persona)                   │     │
│     │ KNOWLEDGE.md  (curated facts)        │     │
│     │ AGENTS.md  (behavior rules)          │     │
│     │ Skills  (prompt-type .md guides)     │     │
│     │ Team Roster  (delegation targets)    │     │
│     │ Memory Context:                      │     │
│     │   - MEMORY.md  (long-term)           │     │
│     │   - Today's log                      │     │
│     │   - Yesterday's log                  │     │
│     │   - Team broadcasts                  │     │
│     └──────────────────────────────────────┘     │
│                                                  │
│  4. ReAct loop (up to 10 rounds):                │
│     ┌──────────────────────────────────┐         │
│     │  LLM streaming call              │         │
│     │         │                        │         │
│     │         ▼                        │         │
│     │  ┌─ text_delta ──► yield to UI   │         │
│     │  │                               │         │
│     │  └─ tool_calls ──► dispatch ─┐   │         │
│     │                              │   │         │
│     │       ┌──────────────────────┘   │         │
│     │       ▼                          │         │
│     │  Execute tools (parallel)        │         │
│     │       │                          │         │
│     │       ▼                          │         │
│     │  Append results to messages      │         │
│     │       │                          │         │
│     │       ▼                          │         │
│     │  Continue loop ──────────────┘   │         │
│     └──────────────────────────────────┘         │
│                                                  │
│  5. Log interaction to daily log                 │
│  6. Re-index embeddings                          │
│  7. Yield stream_end with full response          │
└─────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────┐
│  Gateway broadcasts     │
│  assistant_message to   │
│  all WebSocket clients  │
└─────────────────────────┘
```

### Delegation Flow

When an agent uses the `agent_delegate` tool to hand a task to a teammate:

```
Agent A (e.g. Kit) is processing a user message
         │
         ▼
Agent A calls tool: agent_delegate(agent_id="dev-1", task="...")
         │
         ▼
┌──────────────────────────────┐
│  PersonalAssistant._delegate │
│  1. Check depth < 3          │
│  2. Call SessionManager      │
│     .delegate()              │
└────────┬─────────────────────┘
         │
         ▼
┌──────────────────────────────┐
│  SessionManager.delegate()   │
│  1. get_session(to_agent_id) │   Agent B gets its OWN
│  2. Save task as user msg    │   persistent thread
│  3. _run_and_track(session)  │
│  4. Save response            │
│  5. Broadcast events         │
└────────┬─────────────────────┘
         │
         ▼
Agent B processes the task using its own:
  - Persona (SOUL.md)
  - Tools (scoped subset)
  - Knowledge base
  - Memory (shared workspace)
         │
         ▼
Response returned to Agent A as tool result
Agent A incorporates it into its own reply
```

---

## Component Deep Dives

### Gateway Layer

**File:** `gateway/server.py`

The gateway is a FastAPI application that serves as the single entry point for all client communication.

```
                      Gateway Server
                     (port 18789)

  ┌────────────────────────────────────────────────┐
  │                                                │
  │   HTTP Routes              WebSocket           │
  │  ┌──────────────┐       ┌──────────────┐      │
  │  │ POST /chat   │       │ /ws          │      │
  │  │ POST /stream │       │ - chat_msg   │      │
  │  │ POST /bcast  │       │ - broadcast  │      │
  │  │              │       │ - ping/pong  │      │
  │  │ CRUD /agents │       │ - reactions  │      │
  │  │ CRUD /provdrs│       │              │      │
  │  │ CRUD /skills │       └──────┬───────┘      │
  │  │ CRUD /sched  │              │              │
  │  │ GET  /tools  │              │              │
  │  │ GET  /health │     ConnectionManager       │
  │  │ GET  /config │     ┌──────────────────┐    │
  │  └──────────────┘     │ Set<WebSocket>   │    │
  │                       │ broadcast(msg)   │    │
  │  Agent Knowledge      │ connect/discnct  │    │
  │  ┌──────────────┐     └──────────────────┘    │
  │  │ GET  /agents │                              │
  │  │  /{id}/knowl │     Static Files             │
  │  │ POST search  │     ┌──────────────────┐    │
  │  │ POST docs    │     │ /static → web/   │    │
  │  │ POST urls    │     │ / → index.html   │    │
  │  └──────────────┘     └──────────────────┘    │
  │                                                │
  │  Auth: Bearer token (opt-in via GATEWAY_TOKEN) │
  └────────────────────────────────────────────────┘
```

Key responsibilities:
- **Routing:** Maps HTTP and WebSocket messages to the session manager
- **Broadcasting:** All connected clients receive real-time updates via WebSocket
- **Auth:** Optional bearer token gating (GATEWAY_TOKEN env var)
- **Rate limiting:** Per-user request throttling
- **Static serving:** Hosts the PatternFly web UI

### Session Management

**File:** `gateway/session_manager.py`

```
            Session Manager

  ┌───────────────────────────────────────────┐
  │                                           │
  │  Session ID = {platform}:{user}:{agent}   │
  │                                           │
  │  Examples:                                │
  │    web:anonymous         (Kit, default)   │
  │    web:anonymous:dev-1   (Developer)      │
  │    cli:jkeam             (Kit via CLI)    │
  │    scheduler:daily-check (cron task)      │
  │    dreamer:dream         (dream cycle)    │
  │                                           │
  │  ┌─────────────────────────────────────┐  │
  │  │  sessions: Dict[str, Session]      │  │
  │  │                                     │  │
  │  │  Session {                          │  │
  │  │    session_id                       │  │
  │  │    platform, user_id, agent_id      │  │
  │  │    agent: PersonalAssistant         │  │
  │  │    created_at, last_active          │  │
  │  │  }                                  │  │
  │  └─────────────────────────────────────┘  │
  │                                           │
  │  agent_status: Dict[str, status]          │
  │  activity: deque (last 500 events)        │
  │                                           │
  │  Persistence:                              │
  │    workspace/sessions/{id}.json            │
  │    (advisory file locks for concurrency)   │
  │                                           │
  └───────────────────────────────────────────┘
```

Key behaviors:
- **Lazy creation:** Sessions are created on first message, not ahead of time
- **Agent binding:** Each session is permanently bound to one agent
- **Message persistence:** Chat history is saved to JSON files with advisory locking
- **Activity tracking:** Tool calls and status changes are recorded for the team activity feed

### Agent Runtime

**File:** `runtime/agent.py`

The `PersonalAssistant` class is the core orchestrator. Each session gets one instance.

```
           PersonalAssistant

  ┌──────────────────────────────────────────┐
  │                                          │
  │  Initialization                          │
  │  ┌────────────────────────────────────┐  │
  │  │ LLM Client (OpenAI or LlamaStack) │  │
  │  │ MemoryManager (per-agent)          │  │
  │  │ EmbeddingsManager (shared)         │  │
  │  │ SkillsManager (shared workspace)   │  │
  │  │ KnowledgeManager (per-agent)       │  │
  │  │ MCPManager (per-agent, optional)   │  │
  │  └────────────────────────────────────┘  │
  │                                          │
  │  System Prompt Assembly                  │
  │  ┌────────────────────────────────────┐  │
  │  │ 1. SOUL.md or soul_override       │  │
  │  │ 2. Curated knowledge (KNOWLEDGE)  │  │
  │  │ 3. AGENTS.md (behavior rules)     │  │
  │  │ 4. Prompt skills (.md guides)     │  │
  │  │ 5. Team roster (for delegation)   │  │
  │  │ 6. Memory context                 │  │
  │  │    - MEMORY.md (long-term)        │  │
  │  │    - Today's daily log            │  │
  │  │    - Yesterday's daily log        │  │
  │  │    - Team broadcasts              │  │
  │  └────────────────────────────────────┘  │
  │                                          │
  │  chat_stream() ReAct Loop                │
  │  ┌────────────────────────────────────┐  │
  │  │ for round in range(10):           │  │
  │  │   trim_context(if over limit)     │  │
  │  │   stream = LLM.create(messages)   │  │
  │  │   accumulate text + tool_calls    │  │
  │  │   if tool_calls:                  │  │
  │  │     execute all in parallel       │  │
  │  │     append results, continue      │  │
  │  │   else:                           │  │
  │  │     break (final answer)          │  │
  │  └────────────────────────────────────┘  │
  │                                          │
  │  Tool Dispatch                           │
  │  ┌────────────────────────────────────┐  │
  │  │ agent_delegate  → async _delegate │  │
  │  │ mcp__*          → async MCP call  │  │
  │  │ memory_search   → EmbeddingsManager│ │
  │  │ knowledge_*     → KnowledgeManager │  │
  │  │ skill_*         → SkillsManager    │  │
  │  │ everything else → tools/core.py   │  │
  │  └────────────────────────────────────┘  │
  │                                          │
  │  Guards                                  │
  │  - Per-agent tool filtering (allowlist)  │
  │  - Per-agent skill filtering             │
  │  - Delegation depth limit (max 3)        │
  │  - Context window trimming               │
  │  - Tool result truncation (8KB cap)      │
  │                                          │
  └──────────────────────────────────────────┘
```

### Memory System

**Files:** `runtime/memory.py`, `runtime/embeddings.py`

Three layers, from fastest/smallest to slowest/largest:

```
            Memory System (3 Layers)

  Layer 1: Long-Term Memory (always in system prompt)
  ┌───────────────────────────────────────────┐
  │  workspace/memory/{agent_id}/MEMORY.md    │
  │                                           │
  │  Curated facts, preferences, important    │
  │  information. Written via memory_write    │
  │  tool or edited manually.                 │
  │                                           │
  │  Each agent has its own MEMORY.md.        │
  └───────────────────────────────────────────┘

  Layer 2: Daily Logs (today + yesterday in prompt)
  ┌───────────────────────────────────────────┐
  │  workspace/memory/{agent_id}/YYYY-MM-DD.md│
  │                                           │
  │  Auto-logged conversations. Each turn     │
  │  appends timestamped user/assistant pair.  │
  │  Cleaned up after 7 days (configurable).  │
  │                                           │
  │  Shared broadcasts live in:               │
  │  workspace/memory/broadcasts/YYYY-MM-DD.md│
  └───────────────────────────────────────────┘

  Layer 3: Vector Embeddings (on-demand search)
  ┌───────────────────────────────────────────┐
  │  ChromaDB (workspace/chroma/)             │
  │                                           │
  │  SentenceTransformer (all-MiniLM-L6-v2)   │
  │  indexes MEMORY.md, daily logs, skills.   │
  │                                           │
  │  Queried via memory_search tool.          │
  │  Re-indexed after every memory write.     │
  │  Persistent across restarts.              │
  └───────────────────────────────────────────┘

  Data Flow:
  ┌────────┐    automatic     ┌────────────┐
  │  Chat  │ ──────────────►  │ Daily Log  │
  │  Turn  │                  │ (Layer 2)  │
  └────────┘                  └─────┬──────┘
                                    │ re-index
       memory_write tool            ▼
  ┌────────┐               ┌──────────────┐
  │  LLM   │ ────────────► │  MEMORY.md   │
  │Decision│               │  (Layer 1)   │
  └────────┘               └─────┬────────┘
                                 │ re-index
                                 ▼
                          ┌──────────────┐
                          │   ChromaDB   │
                          │  (Layer 3)   │
                          └──────────────┘
```

### Knowledge System

**File:** `runtime/knowledge.py`

Per-agent knowledge with two layers, separate from the shared memory system:

```
           Knowledge System (Per-Agent)

  ┌──────────────────────────────────────────────┐
  │                                              │
  │  Layer 1: Curated (in system prompt)         │
  │  ┌────────────────────────────────────────┐  │
  │  │  Kit:   workspace/KNOWLEDGE.md         │  │
  │  │  Other: workspace/agents/{id}/KNOWL.md │  │
  │  │                                        │  │
  │  │  Added via knowledge_teach tool or     │  │
  │  │  POST /agents/{id}/knowledge/facts     │  │
  │  └────────────────────────────────────────┘  │
  │                                              │
  │  Layer 2: RAG (searched on demand)           │
  │  ┌────────────────────────────────────────┐  │
  │  │  Per-agent ChromaDB collection         │  │
  │  │  "agent_{id}_knowledge"                │  │
  │  │                                        │  │
  │  │  Sources:                              │  │
  │  │  - Ingested text documents             │  │
  │  │  - Ingested URLs (HTML → Markdown)     │  │
  │  │                                        │  │
  │  │  Stored in:                            │  │
  │  │  workspace/agents/{id}/knowledge/      │  │
  │  │                                        │  │
  │  │  Queried via knowledge_search tool     │  │
  │  └────────────────────────────────────────┘  │
  │                                              │
  └──────────────────────────────────────────────┘
```

### Tool System

**Files:** `tools/core.py`, `tools/web.py`, `tools/browser.py`, `tools/scheduler.py`, `tools/skills.py`, `tools/delegation.py`, `tools/knowledge.py`

```
               Tool Registry

  ┌──────────────────────────────────────────────┐
  │  All tools defined as OpenAI function-call   │
  │  schemas in TOOLS list (tools/core.py)       │
  │                                              │
  │  ┌──────────────────────────────────┐        │
  │  │  Core (tools/core.py)           │        │
  │  │  read, write, list_files        │        │
  │  │  exec_shell                     │        │
  │  │  memory_write, memory_get       │        │
  │  │  memory_search                  │        │
  │  └──────────────────────────────────┘        │
  │  ┌──────────────────────────────────┐        │
  │  │  Web (tools/web.py)             │        │
  │  │  web_search (Tavily API)        │        │
  │  │  web_fetch (httpx + SSRF guard) │        │
  │  └──────────────────────────────────┘        │
  │  ┌──────────────────────────────────┐        │
  │  │  Browser (tools/browser.py)     │        │
  │  │  browser_navigate               │        │
  │  │  browser_screenshot             │        │
  │  │  browser_extract                │        │
  │  │  (runs Playwright in subprocess)│        │
  │  └──────────────────────────────────┘        │
  │  ┌──────────────────────────────────┐        │
  │  │  Scheduler (tools/scheduler.py) │        │
  │  │  schedule_create                │        │
  │  │  schedule_list                  │        │
  │  │  schedule_delete                │        │
  │  └──────────────────────────────────┘        │
  │  ┌──────────────────────────────────┐        │
  │  │  Skills (tools/skills.py)       │        │
  │  │  skill_create, skill_execute    │        │
  │  │  skill_improve, skill_delete    │        │
  │  │  skill_list, skill_info         │        │
  │  └──────────────────────────────────┘        │
  │  ┌──────────────────────────────────┐        │
  │  │  Delegation (tools/delegation.py)│       │
  │  │  agent_delegate                 │        │
  │  └──────────────────────────────────┘        │
  │  ┌──────────────────────────────────┐        │
  │  │  Knowledge (tools/knowledge.py) │        │
  │  │  knowledge_search, knowledge_teach│       │
  │  │  knowledge_ingest, knowledge_list│        │
  │  │  knowledge_ingest_url            │       │
  │  │  knowledge_forget               │        │
  │  └──────────────────────────────────┘        │
  │                                              │
  │  Dispatch: tools/core.py:execute_tool()      │
  │  Exceptions handled in runtime/agent.py:     │
  │  - memory_search → EmbeddingsManager         │
  │  - knowledge_* → KnowledgeManager            │
  │  - skill_* → SkillsManager                   │
  │  - agent_delegate → SessionManager.delegate() │
  │  - mcp__* → MCPManager.call_tool()           │
  │                                              │
  │  Per-Agent Scoping:                          │
  │  agents define tools: ["read", "write", ...]  │
  │  or tools: "*" (unrestricted, Kit default)   │
  └──────────────────────────────────────────────┘
```

### Skills System

**File:** `runtime/skills.py`

Two types of skills, both auto-discovered from `workspace/skills/`:

```
              Skills System

  ┌────────────────────────────────────────────┐
  │                                            │
  │  Executable Skills (.py)                   │
  │  ┌──────────────────────────────────────┐  │
  │  │  - Python scripts with main(**kwargs)│  │
  │  │  - Run in subprocess (isolated)      │  │
  │  │  - Restricted imports (safe modules) │  │
  │  │  - 60s timeout                       │  │
  │  │  - Tracked: usage count, success rate│  │
  │  │  - Versioned with backups            │  │
  │  │                                      │  │
  │  │  Example: word-count.py              │  │
  │  │  def main(**kwargs):                 │  │
  │  │      text = kwargs.get("text", "")   │  │
  │  │      return f"{len(text.split())}"   │  │
  │  └──────────────────────────────────────┘  │
  │                                            │
  │  Prompt Skills (.md)                       │
  │  ┌──────────────────────────────────────┐  │
  │  │  - Markdown with YAML frontmatter    │  │
  │  │  - Injected into system prompt       │  │
  │  │  - NVIDIA SKILL.md format compatible │  │
  │  │  - Not executable, shapes reasoning  │  │
  │  │                                      │  │
  │  │  Example: python-best-practices.md   │  │
  │  │  ---                                 │  │
  │  │  name: python-best-practices         │  │
  │  │  description: Python coding standards│  │
  │  │  ---                                 │  │
  │  │  # Best Practices ...                │  │
  │  └──────────────────────────────────────┘  │
  │                                            │
  │  Discovery: SkillsManager scans            │
  │  workspace/skills/ on init                 │
  │  Metadata: workspace/skills/               │
  │            skills_metadata.json             │
  └────────────────────────────────────────────┘
```

### Provider Registry

**File:** `runtime/providers.py`

```
           Provider Registry

  ┌─────────────────────────────────────────────┐
  │                                             │
  │  workspace/providers/*.json                 │
  │                                             │
  │  ┌────────────────────────────────────────┐ │
  │  │  ProviderConfig {                     │ │
  │  │    id: "default"                      │ │
  │  │    name: "Default"                    │ │
  │  │    type: "ollama"|"openai"|"llamastk" │ │
  │  │    base_url: "http://..."             │ │
  │  │    default_model: "qwen3:14b"         │ │
  │  │    api_key_env: "LLM_API_KEY"         │ │
  │  │    extra_headers_env: "LLM_EXTRA_..."  │ │
  │  │    is_default: true                   │ │
  │  │  }                                    │ │
  │  └────────────────────────────────────────┘ │
  │                                             │
  │  Resolution order for an agent:             │
  │  1. agent.provider matches a provider id    │
  │  2. agent.provider matches a provider type  │
  │  3. Fall back to default (is_default=true)  │
  │  4. Fall back to legacy LLM_* env vars      │
  │                                             │
  │  Secrets stay in .env, referenced by name:  │
  │  api_key_env → os.environ[api_key_env]      │
  │                                             │
  │  Backward compat: auto-creates "default"    │
  │  provider from LLM_* env vars on first run  │
  │                                             │
  └─────────────────────────────────────────────┘
```

### MCP Integration

**File:** `runtime/mcp.py`

```
           MCP (Model Context Protocol)

  ┌──────────────────────────────────────────────┐
  │                                              │
  │  Per-agent MCP server connections.           │
  │  Configured in agent definition:             │
  │  {                                           │
  │    "mcp_servers": {                          │
  │      "server-name": {                        │
  │        "command": "npx",                     │
  │        "args": ["-y", "@some/mcp-server"],   │
  │        "env": {"API_KEY": "${MY_KEY}"}       │
  │      }                                       │
  │    }                                         │
  │  }                                           │
  │                                              │
  │  Lifecycle:                                  │
  │  1. Lazy connect on first chat() call        │
  │  2. Discover tools via MCP list_tools        │
  │  3. Merge into agent's tool list as          │
  │     mcp__{server}__{tool}                    │
  │  4. Route calls back to correct server       │
  │  5. Auto-retry failed servers on next call   │
  │  6. Close on agent shutdown                  │
  │                                              │
  │  Transport: stdio only (subprocess)          │
  │  Env vars: ${VAR} expanded from os.environ   │
  │                                              │
  └──────────────────────────────────────────────┘
```

### Background Services

**Files:** `gateway/scheduler.py`, `gateway/dreamer.py`

```
         Background Services

  ┌──────────────────────────────────────────────┐
  │                                              │
  │  Scheduler (gateway/scheduler.py)            │
  │  ┌────────────────────────────────────────┐  │
  │  │  - Checks every 60 seconds            │  │
  │  │  - 5-field cron expressions            │  │
  │  │  - Each task runs in isolated session: │  │
  │  │    scheduler:{schedule_id}             │  │
  │  │  - Results broadcast via WebSocket     │  │
  │  │  - Tracks last_run, run_count          │  │
  │  │  - Stored: workspace/schedules/        │  │
  │  │    schedules.json                      │  │
  │  └────────────────────────────────────────┘  │
  │                                              │
  │  Dreamer (gateway/dreamer.py)                │
  │  ┌────────────────────────────────────────┐  │
  │  │  - Cron-triggered (default: 3 AM)     │  │
  │  │  - Reviews last N days of daily logs   │  │
  │  │  - Sends reflection prompt to LLM     │  │
  │  │  - Saves dream log to:                │  │
  │  │    workspace/memory/{agent}/dreams/    │  │
  │  │    YYYY-MM-DD.md                      │  │
  │  │  - Produces:                           │  │
  │  │    - Patterns & Themes                 │  │
  │  │    - Insights                          │  │
  │  │    - Unresolved Threads                │  │
  │  │    - Suggested Memories                │  │
  │  │  - Broadcasts dream_complete event     │  │
  │  │                                        │  │
  │  │  Config env vars:                      │  │
  │  │    DREAM_CRON (default: "0 3 * * *")  │  │
  │  │    DREAM_LOOKBACK_DAYS (default: 3)    │  │
  │  │    DREAM_ENABLED (default: true)       │  │
  │  │    DREAM_AGENT_ID (default: kit)       │  │
  │  └────────────────────────────────────────┘  │
  │                                              │
  └──────────────────────────────────────────────┘
```

### Web Frontend

**Files:** `web/index.html`, `web/app.js`, `web/styles.css`

```
           Web UI (PatternFly v5)

  ┌──────────────────────────────────────────────┐
  │                                              │
  │  Single-page app served at /static/          │
  │  Tabs:                                       │
  │  ┌────────────────────────────────────────┐  │
  │  │  Chat     │ Sessions │ Memory │ Tools  │  │
  │  │  Schedules│ Custom Tools & Skills      │  │
  │  └────────────────────────────────────────┘  │
  │                                              │
  │  WebSocket Connection                        │
  │  ┌────────────────────────────────────────┐  │
  │  │  - Connects to /ws                    │  │
  │  │  - Auto-reconnects (2-10s backoff)    │  │
  │  │  - Auth via subprotocol or ?token=    │  │
  │  │                                        │  │
  │  │  Events received:                      │  │
  │  │    connected, user_message,            │  │
  │  │    assistant_message, stream_start,    │  │
  │  │    text_delta, stream_end,             │  │
  │  │    tool_call_start, tool_call_result,  │  │
  │  │    broadcast_reply, broadcast_reactions,│ │
  │  │    agent_status, schedule_run,         │  │
  │  │    dream_complete, message_reaction    │  │
  │  └────────────────────────────────────────┘  │
  │                                              │
  │  Chat Features:                              │
  │  - Per-agent chat threads                    │
  │  - Streaming responses                       │
  │  - Tool call visibility                      │
  │  - Team chat (broadcast mode)                │
  │  - @mentions for targeted replies            │
  │  - Emoji reactions                           │
  │  - Agent status indicators (busy/idle)       │
  │                                              │
  └──────────────────────────────────────────────┘
```

---

## Multi-Agent Architecture

Kit supports a team of specialized agents coordinated by a central "Kit" agent:

```
                Multi-Agent Team

  ┌───────────────────────────────────────────┐
  │                                           │
  │          Kit (Chief of Staff)              │
  │          tools: * (all)                   │
  │          persona: SOUL.md                 │
  │                │                          │
  │     ┌──────────┼──────────┐               │
  │     │          │          │               │
  │     ▼          ▼          ▼               │
  │  ┌───────┐ ┌───────┐ ┌───────┐           │
  │  │dev-1  │ │rsch-1 │ │sec-1  │  ...      │
  │  │David  │ │Ronny  │ │Sally  │           │
  │  │       │ │       │ │       │           │
  │  │tools: │ │tools: │ │tools: │           │
  │  │[scoped│ │[scoped│ │[scoped│           │
  │  │ list] │ │ list] │ │ list] │           │
  │  │       │ │       │ │       │           │
  │  │model: │ │model: │ │model: │           │
  │  │(own or│ │(own or│ │(own or│           │
  │  │default│ │default│ │default│           │
  │  └───────┘ └───────┘ └───────┘           │
  │                                           │
  │  Each agent has:                          │
  │  - Own persona (SOUL.md in agents/{id}/)  │
  │  - Own knowledge base                     │
  │  - Own daily logs & MEMORY.md             │
  │  - Scoped tool access                     │
  │  - Optional custom model/provider         │
  │  - Optional MCP server connections        │
  │  - Separate chat threads per user         │
  │                                           │
  │  Shared across team:                      │
  │  - Workspace directory                    │
  │  - Skills library                         │
  │  - AGENTS.md (behavior rules)             │
  │  - Broadcast messages                     │
  │  - Embeddings model (SentenceTransformer) │
  │                                           │
  │  Created from templates:                  │
  │  templates/agents/*.json (built-in)       │
  │  workspace/agent_templates/*.json (user)  │
  │                                           │
  └───────────────────────────────────────────┘

  Broadcast Flow:

  User posts to team chat
       │
       ├──► All agents generate emoji reactions
       │    (single LLM call picks one per agent)
       │
       └──► Each agent replies from their role
            (randomized order, staggered delay)

  @-Targeted Broadcast:

  User posts "@dev-1 @sec-1 review this code"
       │
       ├──► dev-1 gets full agent pipeline
       │    (tools, knowledge, streaming)
       │
       └──► sec-1 gets full agent pipeline
            (each sees broadcast history as context)
```

---

## Data Storage Layout

```
workspace/
├── SOUL.md                          # Kit's persona
├── AGENTS.md                        # Shared agent behavior rules
├── KNOWLEDGE.md                     # Kit's curated knowledge
├── USER.md.example                  # User preferences template
├── MEMORY.md.example                # Memory template
│
├── memory/
│   ├── kit/                         # Kit's memory
│   │   ├── MEMORY.md                # Kit's long-term memory
│   │   ├── 2026-09-10.md            # Daily logs
│   │   └── dreams/
│   │       └── 2026-09-13.md        # Dream reflections
│   ├── dev-1/                       # dev-1's memory
│   │   ├── MEMORY.md
│   │   └── 2026-09-10.md
│   └── broadcasts/                  # Shared broadcast logs
│       └── 2026-09-10.md
│
├── agents/
│   ├── dev-1.json                   # Agent metadata
│   ├── dev-1/
│   │   ├── SOUL.md                  # Agent persona
│   │   └── knowledge/               # Agent knowledge docs
│   │       └── Ruby-Programming.md
│   ├── researcher-1.json
│   ├── researcher-1/
│   │   ├── SOUL.md
│   │   └── knowledge/
│   └── ...
│
├── agent_templates/                 # User-defined templates
│   ├── joke-1.json
│   └── researcher.json
│
├── sessions/                        # Chat history (JSON)
│   ├── web_browser.json
│   ├── web_browser.json.lock
│   ├── web_browser_dev-1.json
│   └── ...
│
├── providers/                       # LLM provider configs
│   └── default.json
│
├── skills/                          # Custom tools & skills
│   ├── skills_metadata.json
│   ├── count-python-files.py        # Executable skill
│   ├── python-best-practices.md     # Prompt skill
│   └── word-count.py
│
├── schedules/                       # Scheduled tasks
│   └── schedules.json
│
├── chroma/                          # ChromaDB vector storage
│   ├── chroma.sqlite3
│   └── {collection-uuid}/
│
└── tmp/                             # Scratch space

templates/
└── agents/                          # Built-in agent templates
    ├── developer.json
    ├── researcher.json
    ├── security.json
    └── tester.json
```

---

## Security Model

```
            Security Boundaries

  ┌──────────────────────────────────────────────┐
  │  Gateway Auth (opt-in)                       │
  │  ┌────────────────────────────────────────┐  │
  │  │  GATEWAY_TOKEN env var                 │  │
  │  │  - Unset = open access (local dev)     │  │
  │  │  - Set = Bearer token required on all  │  │
  │  │    HTTP routes and WebSocket           │  │
  │  │  - WS auth: subprotocol or ?token=     │  │
  │  │  - Constant-time comparison            │  │
  │  └────────────────────────────────────────┘  │
  │                                              │
  │  File System                                 │
  │  ┌────────────────────────────────────────┐  │
  │  │  read/write/list_files:                │  │
  │  │  Restricted to workspace/ directory    │  │
  │  │  (prevents reading /etc/shadow, .env,  │  │
  │  │   SSH keys, etc.)                      │  │
  │  └────────────────────────────────────────┘  │
  │                                              │
  │  Shell Execution                             │
  │  ┌────────────────────────────────────────┐  │
  │  │  - sudo blocked                        │  │
  │  │  - Shell metacharacters blocked:       │  │
  │  │    & | ; ` $ < >                       │  │
  │  │  - shell=False (no shell injection)    │  │
  │  │  - Blocked commands: rm, mv, dd, mkfs, │  │
  │  │    shutdown, chmod, kill, etc.          │  │
  │  │  - Configurable allowlist/blocklist    │  │
  │  │  - 30s timeout                         │  │
  │  └────────────────────────────────────────┘  │
  │                                              │
  │  Network (tools/net_safety.py)               │
  │  ┌────────────────────────────────────────┐  │
  │  │  web_fetch: SSRF guard blocks         │  │
  │  │  private/link-local/loopback IPs      │  │
  │  └────────────────────────────────────────┘  │
  │                                              │
  │  Skills Sandbox                              │
  │  ┌────────────────────────────────────────┐  │
  │  │  - Run in subprocess (isolated)        │  │
  │  │  - Restricted builtins (no open, eval, │  │
  │  │    exec, compile, __import__)          │  │
  │  │  - Allowlisted imports only:           │  │
  │  │    json, math, re, datetime, etc.      │  │
  │  │  - 60s timeout                         │  │
  │  └────────────────────────────────────────┘  │
  │                                              │
  │  Browser Tools                               │
  │  ┌────────────────────────────────────────┐  │
  │  │  - Run in subprocess (isolated from    │  │
  │  │    FastAPI event loop)                 │  │
  │  │  - 60s timeout                         │  │
  │  └────────────────────────────────────────┘  │
  │                                              │
  │  Agent Scoping                               │
  │  ┌────────────────────────────────────────┐  │
  │  │  - Per-agent tool allowlists           │  │
  │  │  - Per-agent skill allowlists          │  │
  │  │  - Delegation depth limit (max 3)      │  │
  │  │  - Rate limiting per user              │  │
  │  └────────────────────────────────────────┘  │
  │                                              │
  └──────────────────────────────────────────────┘
```
