# Kit User Guide

Kit is a personal AI assistant with a team of specialized agents that work together. You talk to Kit, and Kit delegates tasks to the right team members automatically -- or you can direct them yourself.

This guide covers everything from first-time setup to advanced multi-agent orchestration.

---

## Table of Contents

1. [Getting Started](#getting-started)
2. [Talking to Kit](#talking-to-kit)
3. [Tools](#tools)
4. [Memory](#memory)
5. [The Team](#the-team)
6. [Agent Templates](#agent-templates)
7. [Creating Agents](#creating-agents)
8. [Delegation](#delegation)
9. [Concurrent Delegation](#concurrent-delegation)
10. [Sequential Orchestration](#sequential-orchestration)
11. [Skills](#skills)
12. [Scheduling](#scheduling)
13. [Web UI](#web-ui)
14. [REST API](#rest-api)
15. [Configuration](#configuration)
16. [Troubleshooting](#troubleshooting)

---

## Getting Started

### Prerequisites

- Python 3.14+
- An LLM provider (Ollama, LlamaStack/OGX, or any OpenAI-compatible API)
- ~2GB RAM for embeddings

### Install

```bash
# Using uv (recommended)
uv venv
source .venv/bin/activate
uv sync

# Install browser automation (optional, for browser tools)
playwright install chromium
```

### Configure

```bash
cp .env.example .env
```

Edit `.env` with your LLM provider settings. Pick one:

**Ollama (easiest for local use)**
```
LLM_PROVIDER=ollama
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=qwen3:14b
```

**LlamaStack / OGX**
```
LLM_PROVIDER=llamastack
LLM_BASE_URL=http://localhost:8321
LLM_MODEL=redhat-maas/qwen3-14b
```

**Any OpenAI-compatible API**
```
LLM_PROVIDER=openai
LLM_BASE_URL=https://your-provider.com/v1
LLM_MODEL=your-model-id
LLM_API_KEY=your-key
```

Optional keys:
- `TAVILY_API_KEY` -- required for web search
- `GATEWAY_TOKEN` -- set to require authentication (unset = open for local use)

### Start

```bash
# Terminal 1: Start your LLM provider
ollama serve                          # if using Ollama

# Terminal 2: Start Kit
uv run python -m gateway.server       # starts on http://127.0.0.1:18789
```

Open http://localhost:18789 in your browser, or use the CLI:

```bash
uv run python cli.py "Hello Kit!"
```

---

## Talking to Kit

Kit is the "chief of staff" -- it receives your messages, decides what to do, and coordinates its team to get the work done. You can talk to Kit naturally:

```
What files are in this project?
```

```
Create a file called notes.txt with my meeting notes from today.
```

```
Search my memories for anything about the deployment last week.
```

Kit will use its tools directly for simple tasks, or delegate to a specialist agent when the task matches one of its team members.

### Switching Agents

You can also talk directly to a specific agent through the web UI by switching the active chat target. Each agent maintains its own conversation thread per user, but shares the same workspace memory.

---

## Tools

Kit and its agents have access to 17+ built-in tools, scoped per agent:

### File Operations
| Tool | Description |
|------|-------------|
| `read` | Read file contents |
| `write` | Write or overwrite a file |
| `list_files` | List directory contents |

### System
| Tool | Description |
|------|-------------|
| `exec_shell` | Run a shell command (30s timeout, `sudo` blocked) |

### Memory
| Tool | Description |
|------|-------------|
| `memory_write` | Store a fact in long-term memory (MEMORY.md) |
| `memory_get` | Retrieve full memory context |
| `memory_search` | Semantic search over workspace files |

### Knowledge
| Tool | Description |
|------|-------------|
| `knowledge_teach` | Add a fact to this agent's knowledge base |
| `knowledge_ingest` | Ingest a block of text as a knowledge source |
| `knowledge_ingest_url` | Ingest content from a URL |
| `knowledge_search` | Search the agent's knowledge base |
| `knowledge_list` | List all knowledge sources |
| `knowledge_forget` | Remove a knowledge source |

### Web
| Tool | Description |
|------|-------------|
| `web_search` | Search the web (requires Tavily API key) |
| `web_fetch` | Fetch content from a URL |

### Browser
| Tool | Description |
|------|-------------|
| `browser_navigate` | Navigate to a URL |
| `browser_screenshot` | Take a screenshot of a page |
| `browser_extract` | Extract content from a page |

### Scheduling
| Tool | Description |
|------|-------------|
| `schedule_create` | Create a recurring task |
| `schedule_list` | List all schedules |
| `schedule_delete` | Delete a schedule |

### Skills
| Tool | Description |
|------|-------------|
| `skill_create` | Create a new user-defined skill |
| `skill_list` | List available skills |
| `skill_execute` | Run a skill |
| `skill_improve` | Update an existing skill |
| `skill_delete` | Delete a skill |
| `skill_info` | Get details about a skill |

### Delegation
| Tool | Description |
|------|-------------|
| `agent_delegate` | Hand a task to another agent and wait for their reply |

---

## Memory

Kit uses a 3-layer memory system so it can remember context across conversations:

### Layer 1: Long-term Memory (MEMORY.md)

Curated facts stored in `workspace/MEMORY.md`. Written via the `memory_write` tool or by editing the file directly. Loaded into every conversation.

```
Remember: the production database is on port 5433, not the default 5432.
```

### Layer 2: Daily Logs

Every conversation is automatically logged to `workspace/memory/YYYY-MM-DD.md`. Kit loads today's and yesterday's logs into context automatically, so it remembers recent conversations without being asked.

Logs are cleaned up after 7 days by default.

### Layer 3: Vector Embeddings

All workspace files are indexed with semantic embeddings (ChromaDB + SentenceTransformer). The `memory_search` tool queries this index, so Kit can find relevant information even when the exact wording doesn't match.

```
Search my memories for that thing about the API rate limits.
```

---

## The Team

Kit manages a team of specialized agents. Each agent has its own persona, tool access, and conversation thread. Kit is the default agent -- it has full access to everything and coordinates the rest.

### Viewing the Roster

In the web UI, the **Agents** section shows all configured agents and their status. Via the API:

```bash
curl http://localhost:18789/agents
```

### Built-in Agents

Kit always exists as the built-in manager agent with id `kit`. Additional agents are created from templates.

---

## Agent Templates

Templates are blueprints for creating agents. Kit ships with four built-in templates:

### developer

A software developer that can read, write, and execute code.

- **Tools**: read, write, list_files, exec_shell, memory, knowledge, web, skills
- **Persona**: Precise, minimal diffs, verifies own work

### researcher

A researcher that investigates topics using web search, knowledge bases, and local files. Cannot modify code.

- **Tools**: read, list_files, memory, knowledge, web (no write access, no shell access)
- **Persona**: Thorough, cites sources, synthesizes findings into clear summaries, flags uncertainty

### security

A security reviewer that audits code but cannot modify it.

- **Tools**: read, list_files, exec_shell, memory, knowledge, web (no write access)
- **Persona**: Thinks like an attacker, cites file/line, prioritizes real exploits over theoretical risks

### tester

A tester that runs test suites and reports results but cannot modify code.

- **Tools**: read, list_files, exec_shell, memory, knowledge, web (no write access)
- **Persona**: Reports facts not guesses, covers edge cases, hands failures back for fixing

### The Default Team

Together these four templates cover the core software development loop:

```
researcher  -->  developer  -->  tester
(what to do)    (build it)     (verify it)
                    ^
                    |
                 security
               (audit it)
```

Create one agent from each template and Kit can orchestrate the full pipeline automatically.

### Custom Templates

Save your own templates to `workspace/agent_templates/` as JSON, or use the API:

```bash
curl -X POST http://localhost:18789/agent-templates \
  -H "Content-Type: application/json" \
  -d '{
    "id": "analyst",
    "name": "Analyst",
    "description": "Analyzes data and generates reports.",
    "tools": ["read", "list_files", "exec_shell", "memory_search", "knowledge_search"],
    "skills": "*",
    "soul": "# Analyst\n\nYou analyze data and produce clear, actionable reports."
  }'
```

---

## Creating Agents

Create an agent from a template to add it to the team:

### Quick Start: Create the Full Default Team

The fastest way to get started is the **"Create default team"** button in the sidebar. It creates one agent from each built-in template (researcher, developer, tester, security) in a single click. The button hides itself once all four agents exist.

You can do the same thing via the API:

```bash
curl -X POST http://localhost:18789/agents/create-default-team
```

This is idempotent -- it skips any agent that already exists and creates the rest. The response tells you what was created and what was skipped:

```json
{
  "created": [{"id": "researcher-1", ...}, {"id": "dev-1", ...}],
  "skipped": ["qa-1", "sec-1"]
}
```

Or ask Kit in chat:

```
Create a researcher agent named Ronny, a developer agent named David,
a tester agent named Quinn, and a security agent named Sam.
```

### Via the Web UI

Use the **"Create default team"** button in the sidebar to create all four default agents at once, or use the **"Add teammate"** button to create agents one at a time from any available template.

### Via the API

```bash
curl -X POST http://localhost:18789/agents \
  -H "Content-Type: application/json" \
  -d '{
    "template_id": "developer",
    "id": "dev-1",
    "name": "David",
    "description": "Writes and modifies code, runs commands, and keeps track of implementation notes."
  }'
```

### Via Chat

Ask Kit to do it:

```
Create a new tester agent from the tester template with id "qa-1" and name "Quinn".
```

### Agent Options

When creating an agent, you can override the template defaults:

| Field | Description |
|-------|-------------|
| `template_id` | Which template to base this agent on (required) |
| `id` | Unique identifier for the agent (required) |
| `name` | Display name |
| `description` | What this agent does (shown in Kit's team roster) |
| `tool_overrides` | Override the template's tool list |
| `skill_overrides` | Override the template's skill list |
| `soul_overrides` | Override the template's persona |
| `model` | Use a different LLM model for this agent |
| `provider` | Use a different LLM provider for this agent |
| `mcp_servers` | Attach MCP tool servers to this agent |
| `color` | Display color in the UI |

---

## Delegation

Delegation is how Kit hands tasks to its team members. When Kit receives a request, it checks each teammate's description and delegates to the best match automatically.

### How It Works

1. You send a message to Kit
2. Kit's system prompt includes a "YOUR TEAM" section listing all agents and their descriptions
3. Kit calls `agent_delegate(agent_id, task)` to hand the task to the right agent
4. The target agent works in its own persistent thread for this user
5. The agent's reply comes back to Kit, which presents it to you

### Direct Delegation

You can also tell Kit exactly who to delegate to:

```
Ask David to write a Python script that sorts a list of numbers.
```

```
Have Ronny research the latest changes in the FastAPI 1.0 release.
```

### Delegation Depth

Agents can delegate to other agents too (up to 3 levels deep). This prevents circular delegation while still allowing Kit to coordinate multi-agent chains.

---

## Concurrent Delegation

When you ask Kit for multiple independent things, it can delegate to several agents simultaneously. The agents work in parallel, and Kit collects all the results before responding.

### How It Works

When the LLM emits multiple `agent_delegate` tool calls in a single response, Kit runs them all concurrently using `asyncio.gather`. This means if you ask for two things that map to two different agents, both start working at the same time instead of one waiting for the other.

### Examples

**Two independent tasks, two agents:**
```
Have Ronny research what Python 3.14 new features are, and at the same
time have David list all the Python files in this project.
```

Kit delegates to both agents simultaneously. Ronny researches while David lists files. Both results come back as soon as both are done.

**Two research tasks in parallel:**
```
I need two things done at once: ask Ronny to find out what FastAPI
middleware options exist for rate limiting, and ask David to write a
hello world script at workspace/hello.py.
```

**Explicit parallel phrasing:**
```
In parallel: delegate to Ronny to summarize the contents of
workspace/MEMORY.md, and delegate to David to count how many lines
of code are in runtime/agent.py.
```

### When Does Concurrent Delegation Happen?

It depends on the LLM. Kit doesn't force parallelism -- it runs whatever the model emits in a single response round concurrently. Phrasing your request to clearly describe two independent tasks makes it more likely the model will emit both `agent_delegate` calls at once.

If the model emits them in separate rounds (one per response), they'll still run sequentially. This is a model behavior, not a Kit limitation.

---

## Sequential Orchestration

For tasks where each step depends on the previous one, Kit chains agents in sequence. The ReAct tool-use loop naturally supports this: Kit delegates to one agent, gets the result, then delegates to the next agent with context from the first.

### How It Works

1. Kit delegates to Agent A with the first task
2. Agent A completes and returns a result
3. Kit takes that result and formulates a task for Agent B
4. Agent B completes and returns a result
5. Kit continues the chain or presents the final result

This happens within Kit's normal tool-use loop (up to 10 rounds), with no special configuration needed.

### Examples

**Research then implement:**
```
First have Ronny research how to write a Python script that fetches
the current weather for a given city using a free API. Then hand his
findings to David and have David write the script at
workspace/skills/weather.py.
```

Kit delegates to Ronny first. When Ronny's research comes back, Kit formulates a coding task that includes Ronny's findings and delegates to David.

**Implement then test:**
```
Have David write a function in workspace/skills/fizzbuzz.py that does
fizzbuzz. When he's done, have Quinn review and test his code.
```

Kit delegates to David first, then hands David's output to Quinn for testing.

**Full pipeline -- research, code, test:**
```
I want a three-step pipeline: first Ronny researches best practices
for input validation in Python CLI tools, then David writes a CLI
script at workspace/skills/validator.py based on Ronny's research,
then Quinn tests the script.
```

Each step feeds into the next. Kit manages the handoffs automatically.

**Security review after development:**
```
Have David write a script that accepts user input and queries a
SQLite database. When he's done, have Sam review David's code
for vulnerabilities.
```

### Tips for Sequential Orchestration

- **Be explicit about the order.** Words like "first", "then", "when done", "after that" help the model understand the dependency chain.
- **Reference previous results.** Saying "hand his findings to David" tells Kit to pass context forward.
- **Keep chains reasonable.** The tool-use loop supports up to 10 rounds, so a 3-4 step pipeline is comfortable. Very long chains may hit the round limit.

---

## Combining Concurrent and Sequential

You can mix both patterns in a single request:

**Parallel research, then sequential build and test:**
```
First, in parallel, have Ronny research Python logging best practices
and have Sam audit what security concerns exist with our current
logging. Then have David implement a unified logging setup based on
both findings. Finally, have Quinn test the new logging.
```

This runs Ronny and Sam concurrently in the first step, feeds both results into David for the build step, and then hands off to Quinn for testing.

**Parallel research, then implement:**
```
In parallel, have Ronny research Python input validation libraries
and have David check what validation we already have in the codebase.
Then have David implement a unified approach based on both findings.
```

---

## Skills

Skills are user-created Python scripts that extend Kit's capabilities without modifying Kit's core code.

### How Skills Work

- Skills live in `workspace/skills/` as Python scripts
- They receive JSON via stdin and output JSON via stdout
- Any agent with `skill_execute` in its tool list can run them
- Skills are auto-discovered on startup

### Creating a Skill

Ask Kit or a developer agent:

```
Create a skill called "word-count" that counts the words in a given text.
```

Or create one manually at `workspace/skills/word-count.py`:

```python
import json
import sys

params = json.loads(sys.stdin.read())
text = params.get("text", "")
count = len(text.split())
print(json.dumps({"result": f"{count} words"}))
```

### Using Skills

```
Run the word-count skill with text "hello world foo bar"
```

```
List all available skills.
```

---

## Scheduling

Kit can create recurring tasks that run automatically in the background.

```
Create a schedule to check disk usage every day at 9am.
```

```
List all my schedules.
```

```
Delete the disk-check schedule.
```

Schedules are stored as JSON in `workspace/schedules/`. A background runner checks every 60 seconds and sends due tasks to Kit in an isolated session.

---

## Web UI

The web UI at http://localhost:18789 is built with PatternFly v5 and provides:

- **Chat** -- real-time conversation with Kit (or any agent)
- **Sessions** -- view and switch between active sessions
- **Memory** -- browse and search workspace memory
- **Tools** -- explore available tools and their parameters
- **Schedules** -- manage recurring tasks
- **Agents** -- view, create, and manage team agents

### Real-time Updates

The UI uses WebSocket connections for real-time streaming. All connected tabs receive updates simultaneously. The connection auto-reconnects on disconnect (2-10 second backoff).

### Multi-tab Demo

1. Open http://localhost:18789 in two browser tabs
2. Send a message in tab 1
3. Watch it appear in both tabs instantly

---

## REST API

All Kit functionality is available via REST. All routes require a bearer token when `GATEWAY_TOKEN` is set.

### Chat

```bash
# Send a message (non-streaming)
curl -X POST http://localhost:18789/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What files are in this directory?"}'
```

### Agents

```bash
# List all agents
curl http://localhost:18789/agents

# Get a specific agent
curl http://localhost:18789/agents/dev-1

# Create an agent
curl -X POST http://localhost:18789/agents \
  -H "Content-Type: application/json" \
  -d '{"template_id": "developer", "id": "dev-2", "name": "Dana"}'

# Update an agent
curl -X PUT http://localhost:18789/agents/dev-2 \
  -H "Content-Type: application/json" \
  -d '{"description": "Senior developer specializing in Python."}'

# Delete an agent
curl -X DELETE http://localhost:18789/agents/dev-2

# Create the default team (researcher, developer, tester, security)
curl -X POST http://localhost:18789/agents/create-default-team

# Agent status (online/idle)
curl http://localhost:18789/agents/status

# Agent activity log
curl http://localhost:18789/agents/activity
curl "http://localhost:18789/agents/activity?agent_id=dev-1&limit=50"
```

### Templates

```bash
# List templates
curl http://localhost:18789/agent-templates

# Save a custom template
curl -X POST http://localhost:18789/agent-templates \
  -H "Content-Type: application/json" \
  -d '{"id": "analyst", "name": "Analyst", "description": "...", "tools": [...], "soul": "..."}'

# Delete a custom template
curl -X DELETE http://localhost:18789/agent-templates/analyst
```

### Knowledge (per-agent)

```bash
# List knowledge sources for an agent
curl http://localhost:18789/agents/dev-1/knowledge

# Get curated knowledge
curl http://localhost:18789/agents/dev-1/knowledge/curated
```

---

## Configuration

### Environment Variables (.env)

| Variable | Description | Default |
|----------|-------------|---------|
| `LLM_PROVIDER` | `llamastack`, `ollama`, or `openai` | `llamastack` |
| `LLM_BASE_URL` | LLM server URL | `http://localhost:8321` |
| `LLM_MODEL` | Model ID | `redhat-maas/qwen3-14b` |
| `LLM_API_KEY` | API key (for providers that need one) | -- |
| `LLM_EXTRA_HEADERS` | JSON object of extra HTTP headers | -- |
| `GATEWAY_HOST` | Gateway bind address | `127.0.0.1` |
| `GATEWAY_PORT` | Gateway port | `18789` |
| `GATEWAY_TOKEN` | Bearer token for auth (unset = open) | -- |
| `CORS_ORIGINS` | Allowed CORS origins | `*` |
| `TAVILY_API_KEY` | Tavily API key for web search | -- |
| `MAX_CONTEXT_TOKENS` | Override detected context window | auto-detected |
| `MAX_TOOL_RESULT_CHARS` | Per-tool result size cap | `8000` |

### Tool Safety (config.yaml)

```yaml
tools:
  safety:
    shell_confirm_destructive: true
    allowed_commands:
      - ls
      - cat
      - grep
      # ...
```

### Key Files

| File | Purpose |
|------|---------|
| `workspace/SOUL.md` | Kit's personality and behavior |
| `workspace/AGENTS.md` | Agent behavior rules and delegation instructions |
| `workspace/MEMORY.md` | Long-term curated memory |
| `workspace/USER.md` | User preferences and context |
| `workspace/agents/` | Configured agent instances |
| `workspace/agent_templates/` | User-defined templates |
| `workspace/memory/` | Daily conversation logs |
| `workspace/schedules/` | Scheduled task definitions |
| `workspace/skills/` | User-created skills |

---

## Troubleshooting

### Gateway won't start

```bash
# Check if port is in use
lsof -i :18789

# Kill existing process
pkill -f gateway.server
```

### WebSocket keeps disconnecting

This is normal -- the UI auto-reconnects in 2-10 seconds. Check the browser console (F12) for "WebSocket connected" messages. Persistent disconnects usually mean the gateway process died.

### Slow first message

The SentenceTransformer embedding model downloads on first run (~90MB). Subsequent starts use the cached model.

### Agent delegation not working

1. Verify agents exist: `curl http://localhost:18789/agents`
2. Check that Kit's system prompt includes the "YOUR TEAM" section (it's built dynamically from the agent registry)
3. Make sure the target agent's `tools` list doesn't include `agent_delegate` unless you want sub-delegation -- only Kit needs it by default
4. Check delegation depth: chains deeper than 3 levels are refused

### Browser tools fail

```bash
# Ensure Playwright is installed
playwright install chromium
```

Browser tools run in a subprocess for isolation from FastAPI's event loop.

### Web search returns errors

Web search requires a Tavily API key. Set `TAVILY_API_KEY` in your `.env` file.

### Model can't be reached

```bash
# Test LLM connectivity
curl http://localhost:11434/v1/models    # Ollama
curl http://localhost:8321/models         # LlamaStack
```

Check that `LLM_BASE_URL` in `.env` matches your running provider.
