"""
Agent Runtime - orchestrates LLM calls, tool execution, and memory.

This integrates with LlamaStack (soon OGX) which handles the ReAct loop.
"""

import asyncio
import json
import re
import uuid
from pathlib import Path
from typing import Dict, Any, List, Optional, Set, AsyncGenerator
from llama_stack_client import AsyncLlamaStackClient
from openai import AsyncOpenAI

from runtime.memory import MemoryManager
from runtime.embeddings import EmbeddingsManager
from runtime.harmony import looks_like_harmony, parse_harmony_content, strip_harmony
from runtime.knowledge import KnowledgeManager
from runtime.mcp import MCPManager, parse_mcp_tool_name
from runtime.skills import SkillsManager
from tools.core import TOOLS, execute_tool
from tools.plan import (
    PLAN_TOOL_FUNCTIONS,
    coerce_plan_present_args,
    extract_plan_from_model_output,
    looks_like_plan_json,
    plan_approve,
    plan_cancel,
    plan_complete,
    plan_get,
    plan_present,
    plan_reject,
    plan_revise,
    plan_step_update,
    resolve_plan_goal,
)
from env_config import env_int

MAX_TOOL_ROUNDS = 15

# Plan tools that end the turn after success (show the plan / ask the user).
_PLAN_TERMINAL_TOOLS = frozenset({"plan_present", "plan_revise"})
# Plan tools that may flip Kit between plan and orchestrate modes.
_PLAN_MODE_TOOLS = frozenset({
    "plan_present", "plan_approve", "plan_reject", "plan_complete",
    "plan_revise", "plan_cancel",
})

_THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _clean_assistant_text(text: str) -> str:
    """Strip model-internal markup (think tags, Harmony tokens) for display/logs."""
    if not text:
        return ""
    cleaned = _THINK_TAG_RE.sub("", text)
    cleaned = strip_harmony(cleaned)
    return cleaned.strip()


# Shown when the model finishes with no user-visible text (common with
# gpt-oss: reasoning_content only, or Harmony analysis with no final channel).
EMPTY_REPLY_NOTICE = (
    "I finished processing but produced an empty reply "
    "(the model returned only internal reasoning or no usable text). "
    "Please try sending your message again."
)

# Rough chars-per-token ratio used for estimation (conservative — most
# tokenizers average ~3.5–4 chars/token; using 3 overestimates and errs
# on the side of caution).
_CHARS_PER_TOKEN = 3

# Fallback context-window budget (in tokens) when the model's limit
# can't be detected from the API.  Override with MAX_CONTEXT_TOKENS in
# .env if needed.
_FALLBACK_CONTEXT_TOKENS = env_int("MAX_CONTEXT_TOKENS", 120_000)

# Single tool-result cap (characters).  Prevents one enormous tool
# response from filling the entire context window in a single round.
MAX_TOOL_RESULT_CHARS = env_int("MAX_TOOL_RESULT_CHARS", 8_000)

# Max prior user/assistant turns loaded from session history into each LLM
# call. Token trimming drops older turns further if still over budget.
MAX_HISTORY_MESSAGES = env_int("MAX_HISTORY_MESSAGES", 40)

# Reserve this fraction of the detected context window for the model's
# own output and overhead (tool definition expansion by proxies, etc.).
_CONTEXT_RESERVE_FRACTION = 0.20

# How many hops an agent_delegate chain may take before it's refused. Guards
# against a deliberately-configured delegation cycle (A -> B -> A) recursing
# forever; normal delegation is 1-2 hops deep.
MAX_DELEGATION_DEPTH = 3

# Providers that speak plain OpenAI-compatible chat completions
# (as opposed to "llamastack", which uses the LlamaStack client/server).
OPENAI_COMPATIBLE_PROVIDERS = {"ollama", "openai"}


def _is_bad_request(exc: Exception) -> bool:
    """True if the exception is an HTTP 400 Bad Request from the LLM provider."""
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    return status == 400


class PersonalAssistant:
    """Personal AI Assistant with memory and tool execution."""

    def __init__(
        self,
        base_url: str = "http://localhost:8321",
        model: str = "redhat-maas/qwen3-14b",
        workspace_dir: str = "workspace",
        use_embeddings: bool = True,
        provider: str = "llamastack",
        api_key: Optional[str] = None,
        extra_headers: Optional[Dict[str, str]] = None,
        embeddings: Optional[EmbeddingsManager] = None,
        allowed_tools: Optional[Set[str]] = None,
        allowed_skills: Optional[Set[str]] = None,
        soul_override: Optional[str] = None,
        agent_id: Optional[str] = None,
        session_manager: Optional[Any] = None,
        platform: Optional[str] = None,
        user_id: Optional[str] = None,
        mcp_servers: Optional[Dict[str, dict]] = None,
    ):
        """
        Initialize the assistant.

        Args:
            base_url: LLM server URL (LlamaStack, Ollama, OpenCode Zen, etc.)
            model: Model ID to use
            workspace_dir: Workspace directory for memory files
            use_embeddings: Enable vector embeddings for semantic search
                (ignored if `embeddings` is provided)
            provider: "llamastack" (default) or an OpenAI-compatible
                provider such as "ollama" or "openai"
            api_key: API key for OpenAI-compatible providers that require one
                (e.g. OpenCode Zen). Not needed for LlamaStack or Ollama.
            extra_headers: Extra HTTP headers sent with every LLM request
                (e.g. {"x-opencode-session": "..."} for OpenCode Zen).
            embeddings: A pre-built EmbeddingsManager to share across
                multiple PersonalAssistant instances (e.g. one per session).
                Avoids loading a separate SentenceTransformer model per
                session. If omitted, one is created per `use_embeddings`.
            allowed_tools: Restrict this agent to a subset of the global
                tool registry (by name). None (default) means unrestricted.
                Kit is given an explicit manager allowlist by AgentRegistry.
            allowed_skills: Restrict which named skills this agent may
                execute/list/manage. None (default) means unrestricted.
            soul_override: Persona text to use instead of loading
                workspace/SOUL.md - lets a non-Kit team member have its own
                persona while still sharing this workspace's memory/skills.
            agent_id: This agent's own id in the team roster (e.g. "kit"),
                used to tag messages this agent places into another agent's
                thread via delegation.
            session_manager: A SessionManager-like object (duck-typed to
                avoid a circular import with gateway/session_manager.py)
                used to dispatch `agent_delegate` tool calls. Only agents
                whose allowed_tools include "agent_delegate" can actually
                use it - see `_delegate`.
            platform: The platform this agent's own session belongs to
                (needed so delegation targets the same user's thread).
            user_id: The user id this agent's own session belongs to.
            mcp_servers: MCP server configurations keyed by server name.
                Each value is a dict with 'command', 'args', and optional
                'env'. Servers are started lazily on the first chat() call.
        """
        if provider in OPENAI_COMPATIBLE_PROVIDERS:
            # Any OpenAI-compatible endpoint (Ollama's /v1 endpoint, OpenCode
            # Zen, OpenAI itself, etc.) speaks the standard chat-completions
            # API used below.
            self.client = AsyncOpenAI(
                base_url=base_url,
                api_key=api_key or "not-needed",
                default_headers=extra_headers,
            )
        else:
            self.client = AsyncLlamaStackClient(base_url=base_url, default_headers=extra_headers)
        self.provider = provider
        self.model = model
        self.workspace_dir = Path(workspace_dir)
        self.memory = MemoryManager(workspace_dir, agent_id=agent_id or "kit")

        # Initialize embeddings (Phase 3). Prefer a shared instance (passed
        # in by SessionManager) over creating a new SentenceTransformer per
        # session.
        self.embeddings: Optional[EmbeddingsManager] = embeddings
        if self.embeddings is None and use_embeddings:
            try:
                self.embeddings = EmbeddingsManager(workspace_dir)
                self.embeddings.index_workspace()
            except Exception as e:
                print(f"Warning: Could not initialize embeddings: {e}")
                print("Continuing without semantic search...")

        # Initialize skills manager
        self.skills = SkillsManager(workspace_dir)

        # Initialize per-agent knowledge manager
        self.knowledge: Optional[KnowledgeManager] = None
        try:
            self.knowledge = KnowledgeManager(
                workspace_dir=workspace_dir,
                agent_id=agent_id or "kit",
                embeddings=self.embeddings,
            )
            self.knowledge.index_all()
        except Exception as e:
            print(f"Warning: Could not initialize knowledge manager: {e}")

        # Load system prompts. soul_override lets a non-Kit team member use
        # its own persona instead of this workspace's shared SOUL.md.
        self.soul = soul_override if soul_override is not None else self._load_file("SOUL.md")
        self.agents_md = self._load_file("AGENTS.md")

        # Per-agent tool/skill scoping. None means unrestricted.
        # Kit's allowlist is dynamic: plan vs orchestrate (see _sync_tools_for_mode).
        self.allowed_tools = allowed_tools
        self.allowed_skills = allowed_skills
        self._filtered_tools = (
            TOOLS if allowed_tools is None
            else [t for t in TOOLS if t["function"]["name"] in allowed_tools]
        )

        # Delegation context - who this agent is, and how to reach the rest
        # of the team. `_current_delegation_depth` is set per chat_stream()
        # call (not per instance) since one PersonalAssistant is reused
        # across many unrelated turns.
        self.agent_id = agent_id
        self.session_manager = session_manager
        self.platform = platform
        self.user_id = user_id
        self._current_delegation_depth = 0
        # Fallback when there is no Session (unit tests / bare CLI).
        self.mode = "plan"
        self.active_plan_id: Optional[str] = None

        self.mcp: Optional[MCPManager] = (
            MCPManager(mcp_servers) if mcp_servers else None
        )

        # Context-window limit — resolved lazily on first chat() call
        # (needs an async API call to detect the model's limit).
        self._max_context_tokens: Optional[int] = None

    async def _resolve_context_limit(self) -> int:
        """Detect the model's context window from the API and cache it.

        Tries the OpenAI-compatible ``GET /models/{model}`` endpoint first
        (works with LiteLLM, vLLM, Ollama, etc.). Falls back to the env
        var ``MAX_CONTEXT_TOKENS``, then to a conservative built-in default.
        """
        if self._max_context_tokens is not None:
            return self._max_context_tokens

        detected: Optional[int] = None
        try:
            if self.provider in OPENAI_COMPATIBLE_PROVIDERS:
                model_info = await self.client.models.retrieve(self.model)
                ctx = getattr(model_info, "context_window", None)
                if ctx is None:
                    ctx = getattr(model_info, "max_model_len", None)
                if ctx is None and hasattr(model_info, "model_extra"):
                    extras = model_info.model_extra or {}
                    ctx = extras.get("context_window") or extras.get("max_model_len")
                if isinstance(ctx, (int, float)) and ctx > 0:
                    detected = int(ctx)
        except Exception:
            pass

        if detected:
            self._max_context_tokens = int(detected * (1 - _CONTEXT_RESERVE_FRACTION))
            print(f"📐 Detected context window for {self.model}: {detected} tokens "
                  f"(using {self._max_context_tokens} after {int(_CONTEXT_RESERVE_FRACTION*100)}% reserve)")
        else:
            self._max_context_tokens = _FALLBACK_CONTEXT_TOKENS
            print(f"📐 Could not detect context window for {self.model}, "
                  f"using fallback: {self._max_context_tokens} tokens")

        return self._max_context_tokens

    def _load_file(self, filename: str) -> str:
        """Load a file from workspace directory."""
        file_path = self.workspace_dir / filename
        if file_path.exists():
            return file_path.read_text()
        return ""

    async def _ensure_mcp_connected(self) -> List[str]:
        """Start MCP servers (if configured) on first use, retry any that
        previously failed, and merge their tools into the list sent to the LLM.
        Returns a list of human-readable status messages for surfacing."""
        if self.mcp is None:
            return []

        notices: List[str] = []

        if not self.mcp.connected:
            failures = await self.mcp.connect()
            mcp_tools = self.mcp.get_openai_tools()
            if mcp_tools:
                self._filtered_tools = self._filtered_tools + mcp_tools
            for name, err in failures.items():
                notices.append(f"MCP server '{name}' failed to connect: {err}")
        elif self.mcp._failed:
            recovered, still_failed = await self.mcp.retry_failed()
            if recovered:
                new_tools = self.mcp.get_openai_tools()
                self._filtered_tools = [
                    t for t in self._filtered_tools
                    if not t["function"]["name"].startswith("mcp__")
                ] + new_tools
                notices.append(f"MCP server(s) reconnected: {', '.join(recovered)}")
            for name, err in still_failed.items():
                notices.append(f"MCP server '{name}' still failing: {err}")

        return notices

    def _own_session(self) -> Optional[Any]:
        if not self.session_manager or self.platform is None or self.user_id is None:
            return None
        try:
            from gateway.session_manager import make_session_id
            sid = make_session_id(self.platform, self.user_id, self.agent_id or "kit")
            return self.session_manager.sessions.get(sid)
        except Exception:
            return None

    def _cancel_requested(self) -> bool:
        session = self._own_session()
        if session is None:
            return False
        ev = getattr(session, "cancel_requested", None)
        return bool(ev is not None and ev.is_set())

    def _get_mode(self) -> str:
        session = self._own_session()
        if session is not None:
            return getattr(session, "mode", "plan") or "plan"
        return self.mode or "plan"

    def _set_mode(self, mode: str, plan_id: Optional[str] = None) -> None:
        """Update plan/orchestrate mode and optional active plan id.

        Callers:
        - plan_approve → mode=orchestrate, plan_id=<id>
        - plan_present → mode=plan, plan_id=<new id>
        - plan_reject / plan_complete / plan_cancel → mode=plan, plan_id=None
        """
        self.mode = mode
        self.active_plan_id = plan_id
        session = self._own_session()
        if session is not None:
            session.mode = mode
            session.active_plan_id = plan_id
        self._sync_tools_for_mode()

    def _sync_tools_for_mode(self) -> None:
        """Rebuild Kit's tool schema from session mode. No-op for other agents."""
        if (self.agent_id or "kit") != "kit":
            return
        from runtime.agents import KIT_ORCHESTRATE_TOOLS, KIT_PLAN_TOOLS

        mode = self._get_mode()
        names = set(KIT_ORCHESTRATE_TOOLS if mode == "orchestrate" else KIT_PLAN_TOOLS)
        self.allowed_tools = names
        mcp_tools = [
            t for t in self._filtered_tools
            if t["function"]["name"].startswith("mcp__")
        ]
        self._filtered_tools = [
            t for t in TOOLS if t["function"]["name"] in names
        ] + mcp_tools

    def _team_roster_section(self) -> str:
        """List of teammates, resolved fresh from the registry every turn.

        Shown to anyone who can `agent_delegate`, and to Kit even without
        that tool so he can assign steps in `plan_present`. Empty otherwise.
        """
        if not self.session_manager:
            return ""
        can_delegate = (
            self.allowed_tools is None or "agent_delegate" in self.allowed_tools
        )
        is_kit = (self.agent_id or "kit") == "kit"
        if not can_delegate and not is_kit:
            return ""
        try:
            roster = self.session_manager.agent_registry.list_agents()
        except Exception:
            return ""
        teammates = [a for a in roster if a.id != (self.agent_id or "kit")]
        if not teammates:
            return ""
        mode = self._get_mode()
        if can_delegate:
            intro = (
                "You can hand a task to any of these agents with the agent_delegate tool "
                "(agent_delegate(agent_id, task)) - only that agent acts on it. "
                "Walk the approved plan in dependency order; pass prior step results "
                "in the task text. After each reply, call plan_step_update.\n"
            )
        elif mode == "plan":
            intro = (
                "Assign plan_present steps to these teammates by agent_id. "
                "You cannot delegate or implement yet — present the plan and wait "
                "for the user to approve (then call plan_approve).\n"
            )
        else:
            intro = "Your teammates:\n"
        lines = ["# YOUR TEAM\n", intro]
        for a in teammates:
            lines.append(f"- **{a.id}** ({a.name}): {a.description}")
        return "\n".join(lines)

    def _apply_plan_tool_side_effects(self, tool_name: str, result: str) -> None:
        """Flip Kit session mode based on successful plan tool results."""
        if (self.agent_id or "kit") != "kit":
            return
        if result.startswith("Error"):
            return
        plan_id = None
        for line in result.splitlines():
            if "Plan id:" in line and "`" in line:
                plan_id = line.split("`")[1]
                break
        if tool_name == "plan_approve":
            self._set_mode("orchestrate", plan_id=plan_id)
        elif tool_name in ("plan_reject", "plan_complete", "plan_cancel"):
            self._set_mode("plan", plan_id=None)
        elif tool_name == "plan_present":
            self._set_mode("plan", plan_id=plan_id)
        elif tool_name == "plan_revise" and plan_id:
            session = self._own_session()
            if session is not None:
                session.active_plan_id = plan_id
            else:
                self.active_plan_id = plan_id

    def _run_plan_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> str:
        workspace = str(self.workspace_dir)
        if tool_name == "plan_present":
            kwargs = coerce_plan_present_args(tool_args)
            return plan_present(
                goal=kwargs.get("goal", ""),
                steps=kwargs.get("steps"),
                risks=kwargs.get("risks"),
                open_questions=kwargs.get("open_questions"),
                workspace_dir=workspace,
            )
        if tool_name == "plan_get":
            return plan_get(plan_id=tool_args.get("plan_id"), workspace_dir=workspace)
        if tool_name == "plan_approve":
            return plan_approve(plan_id=tool_args.get("plan_id"), workspace_dir=workspace)
        if tool_name == "plan_revise":
            return plan_revise(
                plan_id=tool_args.get("plan_id"),
                goal=tool_args.get("goal"),
                steps=tool_args.get("steps"),
                risks=tool_args.get("risks"),
                open_questions=tool_args.get("open_questions"),
                workspace_dir=workspace,
            )
        if tool_name == "plan_reject":
            return plan_reject(
                plan_id=tool_args.get("plan_id"),
                reason=tool_args.get("reason"),
                workspace_dir=workspace,
            )
        if tool_name == "plan_step_update":
            return plan_step_update(
                step_id=tool_args.get("step_id", ""),
                status=tool_args.get("status", ""),
                plan_id=tool_args.get("plan_id"),
                result=tool_args.get("result"),
                clarification=tool_args.get("clarification"),
                workspace_dir=workspace,
            )
        if tool_name == "plan_complete":
            return plan_complete(
                plan_id=tool_args.get("plan_id"),
                summary=tool_args.get("summary"),
                workspace_dir=workspace,
            )
        if tool_name == "plan_cancel":
            return plan_cancel(
                plan_id=tool_args.get("plan_id"),
                reason=tool_args.get("reason"),
                workspace_dir=workspace,
            )
        return f"Error: unknown plan tool '{tool_name}'"

    def _build_system_prompt(self) -> str:
        """Build the system prompt from SOUL.md, AGENTS.md, team roster, and memory."""
        parts = []

        if self.soul:
            parts.append(self.soul)

        if self.knowledge:
            curated = self.knowledge.get_curated_knowledge()
            if curated:
                parts.append("\n\n---\n\n")
                parts.append("# KNOWLEDGE BASE\n\n")
                parts.append(curated)

        if self.agents_md:
            parts.append("\n\n---\n\n")
            parts.append(self.agents_md)

        prompt_skills = self.skills.get_prompt_skills(names=self.allowed_skills)
        if prompt_skills:
            parts.append("\n\n---\n\n")
            parts.append("# SKILLS\n\n")
            for skill in prompt_skills:
                parts.append(f"## {skill['name']}\n\n")
                parts.append(skill["content"])
                parts.append("\n\n")

        roster_section = self._team_roster_section()
        if roster_section:
            parts.append("\n\n---\n\n")
            parts.append(roster_section)

        # Add memory context
        memory_context = self.memory.load_context()
        if memory_context:
            parts.append("\n\n---\n\n")
            parts.append("# MEMORY AND CONTEXT\n\n")
            parts.append(memory_context)

        return "\n".join(parts)

    def _reindex_memory(self) -> None:
        """Re-index workspace memory files so semantic search stays current."""
        if self.embeddings:
            try:
                self.embeddings.index_workspace()
            except Exception:
                pass

    def _execute_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> str:
        """Execute a tool call and return the result as a string."""
        if self.allowed_tools is not None and tool_name not in self.allowed_tools:
            return f"Error: tool '{tool_name}' is not available to this agent"

        if (
            self.allowed_skills is not None
            and tool_name in ("skill_execute", "skill_improve", "skill_delete", "skill_info")
            and tool_args.get("name") not in self.allowed_skills
        ):
            return f"Error: skill '{tool_args.get('name')}' is not available to this agent"

        if tool_name == "memory_search":
            if not self.embeddings:
                return "Error: memory_search requires embeddings to be initialized"
            query = tool_args.get("query", "")
            n_results = tool_args.get("n_results", 3)
            search_results = self.embeddings.search(query, n_results)
            formatted = []
            for r in search_results:
                formatted.append(
                    f"[{r['source_type']}] {r['content'][:200]}... "
                    f"(from {Path(r['source']).name})"
                )
            return "\n\n".join(formatted) if formatted else "No relevant memories found"

        if tool_name in PLAN_TOOL_FUNCTIONS:
            result = self._run_plan_tool(tool_name, tool_args)
            self._apply_plan_tool_side_effects(tool_name, result)
            return result

        if tool_name == "knowledge_search":
            if not self.knowledge:
                return "Error: knowledge system not initialized"
            results = self.knowledge.search(
                tool_args.get("query", ""),
                tool_args.get("n_results", 3),
            )
            formatted = []
            for r in results:
                formatted.append(
                    f"[{r['source_type']}] {r['content'][:200]}... "
                    f"(from {Path(r['source']).name})"
                )
            return "\n\n".join(formatted) if formatted else "No relevant knowledge found"
        if tool_name == "knowledge_teach":
            if not self.knowledge:
                return "Error: knowledge system not initialized"
            return self.knowledge.add_fact(tool_args.get("content", ""))
        if tool_name == "knowledge_ingest":
            if not self.knowledge:
                return "Error: knowledge system not initialized"
            return self.knowledge.ingest_text(
                tool_args.get("text", ""),
                tool_args.get("source_name", "unnamed"),
            )
        if tool_name == "knowledge_ingest_url":
            if not self.knowledge:
                return "Error: knowledge system not initialized"
            url = tool_args.get("url", "")
            if not url:
                return "Error: url is required"
            return self.knowledge.ingest_url(url, tool_args.get("source_name", ""))
        if tool_name == "knowledge_list":
            if not self.knowledge:
                return "Error: knowledge system not initialized"
            return self.knowledge.list_sources()
        if tool_name == "knowledge_forget":
            if not self.knowledge:
                return "Error: knowledge system not initialized"
            return self.knowledge.remove_source(tool_args.get("source_name", ""))

        if tool_name == "skill_create":
            params_str = tool_args.get("parameters")
            params = json.loads(params_str) if params_str else None
            tags = tool_args.get("tags", "").split(",") if tool_args.get("tags") else None
            result = self.skills.create_skill(
                name=tool_args["name"],
                description=tool_args["description"],
                code=tool_args["code"],
                parameters=params,
                tags=tags,
                skill_type=tool_args.get("skill_type", "executable"),
            )
            self._reindex_memory()
            return result
        if tool_name == "skill_list":
            return self.skills.list_skills(tool_args.get("tag"), names=self.allowed_skills)
        if tool_name == "skill_execute":
            args_str = tool_args.get("args")
            kwargs = json.loads(args_str) if args_str else {}
            return self.skills.execute_skill(tool_args["name"], **kwargs)
        if tool_name == "skill_improve":
            result = self.skills.improve_skill(
                name=tool_args["name"],
                changes=tool_args["changes"],
                code=tool_args.get("code")
            )
            self._reindex_memory()
            return result
        if tool_name == "skill_delete":
            result = self.skills.delete_skill(tool_args["name"])
            self._reindex_memory()
            return result
        if tool_name == "skill_info":
            info = self.skills.get_skill_info(tool_args["name"])
            return json.dumps(info, indent=2) if info else f"Skill '{tool_args['name']}' not found"

        if tool_name == "exec_shell":
            tool_args = {**tool_args, "cwd": str(self.workspace_dir.resolve())}

        if tool_name in ("memory_write", "memory_get"):
            tool_args = {**tool_args, "agent_id": self.agent_id or "kit"}

        result = execute_tool(tool_name, tool_args)

        if tool_name == "memory_write":
            self._reindex_memory()

        return result

    def _resolve_teammate_id(self, raw: str) -> Optional[str]:
        """Map fuzzy agent ids (e.g. 'researcher') to a real roster id."""
        raw = (raw or "").strip()
        if not raw or not self.session_manager:
            return None
        reg = self.session_manager.agent_registry
        if reg.resolve(raw) is not None:
            return raw
        agents = [a for a in reg.list_agents() if a.id != (self.agent_id or "kit")]
        lower = raw.lower()
        for a in agents:
            if a.id.lower() == lower or a.name.lower() == lower:
                return a.id
        matches: List[str] = []
        for a in agents:
            aid = a.id.lower()
            if aid.startswith(lower + "-") or aid.startswith(lower):
                matches.append(a.id)
            elif (a.template_id or "").lower() == lower:
                matches.append(a.id)
        # Prefer id like researcher-1 over looser matches.
        matches = list(dict.fromkeys(matches))
        if len(matches) == 1:
            return matches[0]
        if matches:
            numbered = [m for m in matches if re.search(r"-\d+$", m)]
            return numbered[0] if numbered else matches[0]
        return None

    def _task_from_plan_step(self, step_id: str) -> str:
        """Build a delegate task from the active plan step when the model omits task."""
        if not step_id:
            return ""
        from tools.plan import load_plan, latest_plan

        workspace = str(self.workspace_dir)
        plan = None
        pid = self.active_plan_id or (self._own_session() and self._own_session().active_plan_id)
        if pid:
            plan = load_plan(str(pid), workspace)
        if plan is None:
            plan = latest_plan(workspace, statuses=["approved", "running", "blocked"])
        if not plan:
            return ""
        for step in plan.get("steps", []):
            if str(step.get("id")) == str(step_id):
                action = (step.get("action") or "").strip()
                criteria = (step.get("success_criteria") or "").strip()
                if criteria:
                    return f"{action}\n\nSuccess criteria: {criteria}"
                return action
        return ""

    def _normalize_delegate_args(self, tool_args: Dict[str, Any]) -> Dict[str, Any]:
        """Repair gpt-oss / Harmony mangled agent_delegate payloads."""
        args = dict(tool_args or {})
        raw_id = str(args.get("agent_id") or args.get("agent") or "").strip()
        resolved = self._resolve_teammate_id(raw_id) if raw_id else None
        if resolved:
            args["agent_id"] = resolved
        task = (
            args.get("task")
            or args.get("description")
            or args.get("action")
            or args.get("message")
            or args.get("instruction")
            or ""
        )
        task = str(task).strip()
        if not task:
            step_id = args.get("step_id") or args.get("id")
            task = self._task_from_plan_step(str(step_id) if step_id else "")
        args["task"] = task
        return args

    def _has_ready_plan_steps(self) -> bool:
        if (self.agent_id or "kit") != "kit" or self._get_mode() != "orchestrate":
            return False
        from tools.plan import load_plan, latest_plan, ready_steps

        workspace = str(self.workspace_dir)
        pid = self.active_plan_id
        session = self._own_session()
        if session is not None and session.active_plan_id:
            pid = session.active_plan_id
        plan = load_plan(str(pid), workspace) if pid else None
        if plan is None:
            plan = latest_plan(workspace, statuses=["approved", "running", "blocked"])
        if not plan or plan.get("status") not in ("approved", "running", "blocked"):
            return False
        return bool(ready_steps(plan))

    async def _delegate(self, tool_args: Dict[str, Any]) -> str:
        """Hand a task to another agent on the team and return its reply.

        Runs through the injected `session_manager` so the delegated task
        lands in the *target agent's own persistent thread for this same
        user* (not a throwaway session) - the same thread the user would
        see if they switched their chat target to that agent.
        """
        if self.allowed_tools is not None and "agent_delegate" not in self.allowed_tools:
            return "Error: tool 'agent_delegate' is not available to this agent"
        if not self.session_manager or self.platform is None or self.user_id is None:
            return "Error: delegation is not available in this context"
        if self._current_delegation_depth >= MAX_DELEGATION_DEPTH:
            return "Error: delegation depth limit reached - avoid configuring delegation cycles"

        tool_args = self._normalize_delegate_args(tool_args)
        target_agent_id = tool_args.get("agent_id")
        task = tool_args.get("task", "")
        if not target_agent_id:
            return "Error: agent_delegate requires 'agent_id'"
        if self.session_manager.agent_registry.resolve(str(target_agent_id)) is None:
            return (
                f"Error: unknown agent '{target_agent_id}'. "
                "Use an exact id from YOUR TEAM (e.g. researcher-1)."
            )
        if not task:
            return "Error: agent_delegate requires a non-empty 'task'"

        try:
            return await self.session_manager.delegate(
                platform=self.platform,
                user_id=self.user_id,
                from_agent_id=self.agent_id or "kit",
                to_agent_id=str(target_agent_id),
                task=str(task),
                depth=self._current_delegation_depth + 1,
            )
        except Exception as e:
            return f"Error delegating to '{target_agent_id}': {e}"

    async def _execute_tool_async(self, tool_name: str, tool_args: Dict[str, Any]) -> str:
        """Dispatch a tool call, awaiting `agent_delegate` and MCP tools
        directly (both are async), and running everything else in a thread
        (existing behavior - shell/browser/skills calls are sync/blocking).
        """
        if tool_name == "agent_delegate":
            return await self._delegate(tool_args)
        # Plan tools mutate session mode / tool allowlists — keep on the
        # event loop, not in a worker thread.
        if tool_name in PLAN_TOOL_FUNCTIONS:
            return self._execute_tool(tool_name, tool_args)
        if self.mcp:
            parsed = parse_mcp_tool_name(tool_name)
            if parsed:
                server_name, real_tool_name = parsed
                return await self.mcp.call_tool(server_name, real_tool_name, tool_args)
        return await asyncio.to_thread(self._execute_tool, tool_name, tool_args)

    @staticmethod
    def _estimate_tokens(messages: list, tools: list) -> int:
        """Rough token estimate for the full LLM request payload."""
        total_chars = sum(len(json.dumps(t)) for t in tools)
        for m in messages:
            content = m.get("content") or ""
            total_chars += len(content)
            for tc in m.get("tool_calls", []):
                total_chars += len(tc.get("function", {}).get("arguments", ""))
        return total_chars // _CHARS_PER_TOKEN

    @staticmethod
    def _trim_context(messages: list, tools: list, limit: int) -> None:
        """Shrink the request until under `limit` tokens. Mutates `messages`.

        Order: shorten oversized tool results, then drop the oldest non-system
        messages (keeping the system prompt and the latest user turn).
        """
        while PersonalAssistant._estimate_tokens(messages, tools) > limit:
            trimmed = False
            for m in messages:
                if m.get("role") == "tool" and len(m.get("content", "")) > 200:
                    m["content"] = m["content"][:200] + "\n[truncated to fit context window]"
                    trimmed = True
                    break
            if trimmed:
                continue
            # system (0) + at least the current user message must remain
            if len(messages) <= 2:
                break
            drop_idx = 1
            dropped_role = messages[drop_idx].get("role")
            del messages[drop_idx]
            # Drop orphaned tool results that belonged to a removed assistant turn
            if dropped_role == "assistant":
                while (
                    drop_idx < len(messages) - 1
                    and messages[drop_idx].get("role") == "tool"
                ):
                    del messages[drop_idx]

    def _prior_messages(self, user_message: str) -> List[Dict[str, str]]:
        """Load prior user/assistant turns for this session from disk.

        Returns [] when there is no session manager (unit tests / bare CLI)
        or no persisted history yet.
        """
        sm = self.session_manager
        if sm is None or not self.platform or not self.user_id:
            return []
        fn = getattr(sm, "llm_history_for", None)
        if fn is None:
            return []
        try:
            return fn(
                self.platform,
                self.user_id,
                self.agent_id or "kit",
                current_user_message=user_message,
                limit=MAX_HISTORY_MESSAGES,
            )
        except Exception:
            return []

    @staticmethod
    def _cap_tool_result(result: str) -> str:
        """Truncate a single tool result if it exceeds the per-result cap."""
        if len(result) > MAX_TOOL_RESULT_CHARS:
            return result[:MAX_TOOL_RESULT_CHARS] + f"\n[truncated — result was {len(result)} chars]"
        return result

    async def chat(self, user_message: str, _delegation_depth: int = 0) -> str:
        """
        Send a message and get a complete response (non-streaming).
        """
        full_response = ""
        async for event in self.chat_stream(user_message, _delegation_depth=_delegation_depth):
            if event["type"] == "stream_end":
                full_response = event["content"]
            elif event["type"] == "stream_error":
                full_response = f"Error: {event['error']}"
        return full_response

    async def chat_stream(
        self, user_message: str, _delegation_depth: int = 0
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Send a message and yield streaming events.

        Yields dicts with "type" key:
            stream_start  - response is beginning
            text_delta    - incremental text content {"content": str}
            tool_call_start  - tool invoked {"tool_name": str, "tool_args": dict}
            tool_call_result - tool finished {"tool_name": str, "result": str}
            stream_end    - done {"content": str}  (full assembled text)
            stream_error  - error {"error": str}
        """
        self._current_delegation_depth = _delegation_depth
        terminal_plan_outputs: list[str] = []
        try:
            self._sync_tools_for_mode()
            mcp_notices = await self._ensure_mcp_connected()
            context_limit = await self._resolve_context_limit()
            system_prompt = self._build_system_prompt()
            mode = self._get_mode()
            if (self.agent_id or "kit") == "kit":
                system_prompt += (
                    f"\n\n---\n\n# SESSION MODE\n\n"
                    f"Current mode: **{mode}**.\n"
                )
                if mode == "plan":
                    system_prompt += (
                        "Present or revise plans. On user approval call plan_approve. "
                        "Do not implement or delegate until approved.\n"
                    )
                else:
                    system_prompt += (
                        "Orchestrate the approved plan with agent_delegate and "
                        "plan_step_update. Handle teammate clarifications. "
                        "Call plan_complete when every step is done. "
                        "For a new unrelated request, plan_present (supersedes) or plan_reject first.\n"
                    )
            messages: List[Dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
            ]
            messages.extend(self._prior_messages(user_message))
            messages.append({"role": "user", "content": user_message})

            yield {"type": "stream_start"}

            for notice in mcp_notices:
                yield {"type": "mcp_notice", "content": notice}

            all_content_parts: list[str] = []
            tools_for_llm = self._filtered_tools or None
            # Track whether this turn made orchestration progress; if not,
            # nudge once so gpt-oss cannot "claim" work and leave the plan stuck.
            # Only auto-nudge when we *started* in orchestrate (not the same
            # turn that just approved the plan).
            started_orchestrating = self._get_mode() == "orchestrate"
            orch_progress = False
            orch_nudged = False

            for _round in range(MAX_TOOL_ROUNDS):
                if self._cancel_requested():
                    yield {"type": "stream_error", "error": "Cancelled"}
                    return

                self._trim_context(messages, self._filtered_tools, context_limit)
                try:
                    stream = await self.client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        tools=tools_for_llm,
                        stream=True,
                    )
                except Exception as _tool_err:
                    if tools_for_llm is not None and _is_bad_request(_tool_err):
                        tools_for_llm = None
                        stream = await self.client.chat.completions.create(
                            model=self.model,
                            messages=messages,
                            stream=True,
                        )
                    else:
                        raise

                content_parts: list[str] = []
                tool_calls_acc: dict[int, dict] = {}
                finish_reason = None
                # Once Harmony control tokens appear, stop streaming raw deltas
                # so <|start|>/<|call|> junk does not flash in the UI.
                harmony_streaming = False

                async for chunk in stream:
                    if self._cancel_requested():
                        yield {"type": "stream_error", "error": "Cancelled"}
                        return
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    delta = choice.delta

                    if delta and delta.content:
                        content_parts.append(delta.content)
                        if not harmony_streaming:
                            joined = "".join(content_parts)
                            if (
                                looks_like_harmony(joined)
                                or looks_like_plan_json(joined)
                            ):
                                harmony_streaming = True
                        if not harmony_streaming:
                            yield {"type": "text_delta", "content": delta.content}

                    if delta and hasattr(delta, "tool_calls") and delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in tool_calls_acc:
                                tool_calls_acc[idx] = {"id": "", "name": "", "arguments": ""}
                            if tc.id:
                                tool_calls_acc[idx]["id"] = tc.id
                            if tc.function:
                                if tc.function.name:
                                    tool_calls_acc[idx]["name"] = tc.function.name
                                if tc.function.arguments:
                                    tool_calls_acc[idx]["arguments"] += tc.function.arguments

                    if choice.finish_reason:
                        finish_reason = choice.finish_reason

                round_content = "".join(content_parts)
                visible_round, harmony_calls = parse_harmony_content(round_content)

                assistant_tool_calls = []
                if tool_calls_acc:
                    for idx in sorted(tool_calls_acc.keys()):
                        tc = tool_calls_acc[idx]
                        assistant_tool_calls.append({
                            "id": tc["id"],
                            "type": "function",
                            "function": {"name": tc["name"], "arguments": tc["arguments"]},
                        })
                elif harmony_calls:
                    # Proxy leaked Harmony tool calls into content instead of
                    # structured tool_calls — recover and execute them.
                    assistant_tool_calls = harmony_calls

                # Model-agnostic fallback: if the model described a plan in JSON
                # or markdown instead of calling plan_present, synthesize the tool call.
                has_plan_present = any(
                    (tc.get("function") or {}).get("name") == "plan_present"
                    for tc in assistant_tool_calls
                )
                if not has_plan_present:
                    recovered = extract_plan_from_model_output(
                        round_content, fallback_goal=user_message
                    )
                    if recovered:
                        assistant_tool_calls = [{
                            "id": f"harmony_{uuid.uuid4().hex[:12]}",
                            "type": "function",
                            "function": {
                                "name": "plan_present",
                                "arguments": json.dumps(recovered),
                            },
                        }]
                        visible_round = ""
                        harmony_streaming = True

                # If Harmony/JSON suppressed mid-stream deltas, flush recovered
                # user-facing text once (e.g. final-channel answer). Skip when
                # we already streamed a plain-text prefix before the first `<|`.
                if harmony_streaming and visible_round:
                    raw_prefix = round_content.split("<|", 1)[0]
                    if not raw_prefix.strip() and not looks_like_plan_json(round_content):
                        yield {"type": "text_delta", "content": visible_round}
                if visible_round and not (
                    assistant_tool_calls
                    and any(
                        (tc.get("function") or {}).get("name") == "plan_present"
                        for tc in assistant_tool_calls
                    )
                ):
                    all_content_parts.append(visible_round)

                if assistant_tool_calls:
                    if self._cancel_requested():
                        yield {"type": "stream_error", "error": "Cancelled"}
                        return

                    messages.append({
                        "role": "assistant",
                        "content": visible_round or None,
                        "tool_calls": assistant_tool_calls,
                    })

                    parsed_calls = []
                    for tc_msg in assistant_tool_calls:
                        tool_name = tc_msg["function"]["name"]
                        try:
                            tool_args = json.loads(tc_msg["function"]["arguments"] or "{}")
                        except json.JSONDecodeError:
                            tool_args = {}
                        if not isinstance(tool_args, dict):
                            tool_args = {}
                        if tool_name == "plan_present":
                            tool_args = coerce_plan_present_args(tool_args)
                            tool_args["goal"] = resolve_plan_goal(
                                tool_args.get("goal"),
                                tool_args.get("steps")
                                if isinstance(tool_args.get("steps"), list)
                                else None,
                                fallback_goal=user_message,
                            )
                        parsed_calls.append((tc_msg, tool_name, tool_args))

                    for _, tool_name, tool_args in parsed_calls:
                        yield {"type": "tool_call_start", "tool_name": tool_name, "tool_args": tool_args}

                    async def _run_tool(name: str, args: Dict[str, Any]) -> str:
                        if self._cancel_requested():
                            return "Error: Cancelled"
                        try:
                            return self._cap_tool_result(
                                str(await self._execute_tool_async(name, args))
                            )
                        except asyncio.CancelledError:
                            raise
                        except Exception as e:
                            return f"Error: {e}"

                    try:
                        results = await asyncio.gather(
                            *(_run_tool(name, args) for _, name, args in parsed_calls)
                        )
                    except asyncio.CancelledError:
                        yield {"type": "stream_error", "error": "Cancelled"}
                        return

                    clarification_stops: list[str] = []
                    for (tc_msg, tool_name, tool_args), result in zip(parsed_calls, results):
                        yield {"type": "tool_call_result", "tool_name": tool_name, "result": result}
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc_msg["id"],
                            "content": result,
                        })
                        if (
                            tool_name in _PLAN_TERMINAL_TOOLS
                            and not str(result).startswith("Error")
                        ):
                            terminal_plan_outputs.append(str(result))
                        if (
                            tool_name == "plan_step_update"
                            and tool_args.get("status") == "needs_clarification"
                            and not str(result).startswith("Error")
                        ):
                            clarification_stops.append(str(result))
                        if (
                            tool_name in ("agent_delegate", "plan_step_update")
                            and not str(result).startswith("Error")
                        ):
                            orch_progress = True

                    # Refresh tools after mode-changing plan tools (e.g. approve → orchestrate).
                    if any(name in _PLAN_MODE_TOOLS for _, name, _ in parsed_calls):
                        tools_for_llm = self._filtered_tools or None

                    if terminal_plan_outputs or clarification_stops:
                        outputs = terminal_plan_outputs + clarification_stops
                        combined = visible_round or ""
                        extras = [md for md in outputs if md.strip() not in combined]
                        if extras:
                            extra = "\n\n".join(extras)
                            yield {"type": "text_delta", "content": extra}
                            all_content_parts.append(extra)
                        break

                    continue

                # No tool calls this round. If Kit started this turn already
                # orchestrating and never delegated/updated, force recovery.
                if (
                    started_orchestrating
                    and not orch_progress
                    and not orch_nudged
                    and (self.agent_id or "kit") == "kit"
                    and self._get_mode() == "orchestrate"
                    and self._has_ready_plan_steps()
                ):
                    orch_nudged = True
                    messages.append({
                        "role": "user",
                        "content": (
                            "ORCHESTRATION STALLED: ready plan steps are still pending. "
                            "Immediately call agent_delegate with the exact agent_id from "
                            "the plan / YOUR TEAM (e.g. researcher-1, not 'researcher') "
                            "and task set to that step's action. After the teammate replies, "
                            "call plan_step_update. Do not claim progress without tools."
                        ),
                    })
                    continue

                break

            full_response = _clean_assistant_text("".join(all_content_parts))
            visible_text = full_response

            if not visible_text:
                used_tools = any(m.get("role") == "tool" for m in messages)
                if (
                    started_orchestrating
                    and (self.agent_id or "kit") == "kit"
                    and self._get_mode() == "orchestrate"
                    and self._has_ready_plan_steps()
                    and not orch_progress
                ):
                    empty_prompt = (
                        "Your response was empty and ready plan steps are still pending. "
                        "Call agent_delegate now with the exact agent_id from the plan "
                        "(e.g. researcher-1) and a clear task, then plan_step_update."
                    )
                elif used_tools:
                    empty_prompt = (
                        "You used tools and got results but your response was empty. "
                        "Please provide a clear answer to the original question based "
                        "on the tool results you received."
                    )
                else:
                    empty_prompt = (
                        "Your response was empty. Please provide a clear, "
                        "visible answer to the user's question. Do not respond "
                        "with only internal reasoning."
                    )
                messages.append({
                    "role": "user",
                    "content": empty_prompt,
                })
                self._trim_context(messages, self._filtered_tools, context_limit)
                try:
                    retry_stream = await self.client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        stream=True,
                    )
                    retry_parts: list[str] = []
                    async for chunk in retry_stream:
                        if not chunk.choices:
                            continue
                        delta = chunk.choices[0].delta
                        if delta and delta.content:
                            retry_parts.append(delta.content)
                            yield {"type": "text_delta", "content": delta.content}
                    retry_text = "".join(retry_parts)
                    retry_visible = _clean_assistant_text(retry_text)
                    if retry_visible:
                        full_response = retry_visible
                except Exception as retry_err:
                    error_msg = str(retry_err)
                    self.memory.log_interaction(
                        user_message, f"ERROR: {error_msg}", speaker=self._log_speaker()
                    )
                    self._reindex_memory()
                    yield {"type": "stream_error", "error": error_msg}
                    return

                if not full_response:
                    full_response = EMPTY_REPLY_NOTICE
                    yield {"type": "text_delta", "content": full_response}

            self.memory.log_interaction(user_message, full_response, speaker=self._log_speaker())
            self._reindex_memory()
            yield {"type": "stream_end", "content": full_response}

        except Exception as e:
            error_msg = str(e)
            self.memory.log_interaction(user_message, f"ERROR: {error_msg}", speaker=self._log_speaker())
            self._reindex_memory()
            yield {"type": "stream_error", "error": error_msg}

    def _log_speaker(self) -> str:
        """Label used in the shared daily log for this agent's replies -
        "Assistant" for Kit (unchanged format), or "<name> (agent)" for a
        team member, so every agent's shared memory context makes clear who
        did what."""
        if not self.agent_id or self.agent_id == "kit":
            return "Assistant"
        return f"{self.agent_id} (agent)"

    async def close(self) -> None:
        """Shut down MCP server connections (if any)."""
        if self.mcp:
            await self.mcp.close()

    def get_memory_stats(self) -> Dict[str, Any]:
        """Get statistics about memory usage."""
        return {
            "memory_file_exists": self.memory.memory_file.exists(),
            "memory_file_size": self.memory.memory_file.stat().st_size if self.memory.memory_file.exists() else 0,
            "recent_logs": self.memory.get_recent_logs(7),
            "workspace_dir": str(self.workspace_dir.absolute())
        }
