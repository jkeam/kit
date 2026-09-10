"""
Agent Runtime - orchestrates LLM calls, tool execution, and memory.

This integrates with LlamaStack (soon OGX) which handles the ReAct loop.
"""

import asyncio
import json
from pathlib import Path
from typing import Dict, Any, List, Optional, Set, AsyncGenerator
from llama_stack_client import AsyncLlamaStackClient
from openai import AsyncOpenAI

from runtime.memory import MemoryManager
from runtime.embeddings import EmbeddingsManager
from runtime.mcp import MCPManager, parse_mcp_tool_name
from runtime.skills import SkillsManager
from tools.core import TOOLS, execute_tool

MAX_TOOL_ROUNDS = 10

# How many hops an agent_delegate chain may take before it's refused. Guards
# against a deliberately-configured delegation cycle (A -> B -> A) recursing
# forever; normal delegation is 1-2 hops deep.
MAX_DELEGATION_DEPTH = 3

# Providers that speak plain OpenAI-compatible chat completions
# (as opposed to "llamastack", which uses the LlamaStack client/server).
OPENAI_COMPATIBLE_PROVIDERS = {"ollama", "openai"}


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
                tool registry (by name). None (default) means unrestricted,
                which preserves Kit's original full-access behavior.
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

        # Load system prompts. soul_override lets a non-Kit team member use
        # its own persona instead of this workspace's shared SOUL.md.
        self.soul = soul_override if soul_override is not None else self._load_file("SOUL.md")
        self.agents_md = self._load_file("AGENTS.md")

        # Per-agent tool/skill scoping. None means unrestricted (Kit's
        # original behavior). Computed once since the allowlist is static
        # for the lifetime of this instance.
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

        self.mcp: Optional[MCPManager] = (
            MCPManager(mcp_servers) if mcp_servers else None
        )

    def _load_file(self, filename: str) -> str:
        """Load a file from workspace directory."""
        file_path = self.workspace_dir / filename
        if file_path.exists():
            return file_path.read_text()
        return ""

    async def _ensure_mcp_connected(self) -> None:
        """Start MCP servers (if configured) on first use and merge their
        tools into the list sent to the LLM."""
        if self.mcp is None or self.mcp.connected:
            return
        await self.mcp.connect()
        self._filtered_tools = self._filtered_tools + self.mcp.get_openai_tools()

    def _team_roster_section(self) -> str:
        """List of teammates this agent can hand tasks to via agent_delegate,
        resolved fresh from the registry every time the system prompt is
        rebuilt (i.e. every turn) so a newly-created agent becomes visible
        immediately - no restart, no stale snapshot taken at construction
        time. Empty for agents that don't have agent_delegate at all."""
        if not self.session_manager:
            return ""
        if self.allowed_tools is not None and "agent_delegate" not in self.allowed_tools:
            return ""
        try:
            roster = self.session_manager.agent_registry.list_agents()
        except Exception:
            return ""
        teammates = [a for a in roster if a.id != (self.agent_id or "kit")]
        if not teammates:
            return ""
        lines = [
            "# YOUR TEAM\n",
            "You can hand a task to any of these agents with the agent_delegate tool "
            "(agent_delegate(agent_id, task)) - only that agent acts on it:\n",
        ]
        for a in teammates:
            lines.append(f"- **{a.id}** ({a.name}): {a.description}")
        return "\n".join(lines)

    def _build_system_prompt(self) -> str:
        """Build the system prompt from SOUL.md, AGENTS.md, team roster, and memory."""
        parts = []

        if self.soul:
            parts.append(self.soul)

        if self.agents_md:
            parts.append("\n\n---\n\n")
            parts.append(self.agents_md)

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

        if tool_name == "skill_create":
            params_str = tool_args.get("parameters")
            params = json.loads(params_str) if params_str else None
            tags = tool_args.get("tags", "").split(",") if tool_args.get("tags") else None
            return self.skills.create_skill(
                name=tool_args["name"],
                description=tool_args["description"],
                code=tool_args["code"],
                parameters=params,
                tags=tags
            )
        if tool_name == "skill_list":
            return self.skills.list_skills(tool_args.get("tag"), names=self.allowed_skills)
        if tool_name == "skill_execute":
            args_str = tool_args.get("args")
            kwargs = json.loads(args_str) if args_str else {}
            return self.skills.execute_skill(tool_args["name"], **kwargs)
        if tool_name == "skill_improve":
            return self.skills.improve_skill(
                name=tool_args["name"],
                changes=tool_args["changes"],
                code=tool_args.get("code")
            )
        if tool_name == "skill_delete":
            return self.skills.delete_skill(tool_args["name"])
        if tool_name == "skill_info":
            info = self.skills.get_skill_info(tool_args["name"])
            return json.dumps(info, indent=2) if info else f"Skill '{tool_args['name']}' not found"

        if tool_name in ("memory_write", "memory_get"):
            tool_args = {**tool_args, "agent_id": self.agent_id or "kit"}

        return execute_tool(tool_name, tool_args)

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

        target_agent_id = tool_args.get("agent_id")
        task = tool_args.get("task", "")
        if not target_agent_id:
            return "Error: agent_delegate requires 'agent_id'"

        try:
            return await self.session_manager.delegate(
                platform=self.platform,
                user_id=self.user_id,
                from_agent_id=self.agent_id or "kit",
                to_agent_id=target_agent_id,
                task=task,
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
        if self.mcp:
            parsed = parse_mcp_tool_name(tool_name)
            if parsed:
                server_name, real_tool_name = parsed
                return await self.mcp.call_tool(server_name, real_tool_name, tool_args)
        return await asyncio.to_thread(self._execute_tool, tool_name, tool_args)

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
        try:
            await self._ensure_mcp_connected()
            system_prompt = self._build_system_prompt()
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ]

            yield {"type": "stream_start"}

            all_content_parts: list[str] = []

            for _round in range(MAX_TOOL_ROUNDS):
                stream = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=self._filtered_tools,
                    stream=True,
                )

                content_parts: list[str] = []
                tool_calls_acc: dict[int, dict] = {}
                finish_reason = None

                async for chunk in stream:
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    delta = choice.delta

                    if delta and delta.content:
                        content_parts.append(delta.content)
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
                all_content_parts.append(round_content)

                if finish_reason == "tool_calls" and tool_calls_acc:
                    assistant_tool_calls = []
                    for idx in sorted(tool_calls_acc.keys()):
                        tc = tool_calls_acc[idx]
                        assistant_tool_calls.append({
                            "id": tc["id"],
                            "type": "function",
                            "function": {"name": tc["name"], "arguments": tc["arguments"]},
                        })

                    messages.append({
                        "role": "assistant",
                        "content": round_content or None,
                        "tool_calls": assistant_tool_calls,
                    })

                    for tc_msg in assistant_tool_calls:
                        tool_name = tc_msg["function"]["name"]
                        try:
                            tool_args = json.loads(tc_msg["function"]["arguments"])
                        except json.JSONDecodeError:
                            tool_args = {}

                        yield {"type": "tool_call_start", "tool_name": tool_name, "tool_args": tool_args}

                        try:
                            # Tool execution (shell, browser, skills) is
                            # synchronous and can block for seconds; run it
                            # in a thread so it doesn't stall the event loop.
                            # agent_delegate is the one exception - it runs a
                            # nested async chat_stream and is awaited directly.
                            result = str(await self._execute_tool_async(tool_name, tool_args))
                        except Exception as e:
                            result = f"Error: {e}"

                        yield {"type": "tool_call_result", "tool_name": tool_name, "result": result}

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc_msg["id"],
                            "content": result,
                        })

                    continue

                break

            full_response = "".join(all_content_parts)
            self.memory.log_interaction(user_message, full_response, speaker=self._log_speaker())
            yield {"type": "stream_end", "content": full_response}

        except Exception as e:
            error_msg = str(e)
            self.memory.log_interaction(user_message, f"ERROR: {error_msg}", speaker=self._log_speaker())
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
