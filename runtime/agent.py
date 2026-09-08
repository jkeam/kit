"""
Agent Runtime - orchestrates LLM calls, tool execution, and memory.

This integrates with LlamaStack (soon OGX) which handles the ReAct loop.
"""

import json
from pathlib import Path
from typing import Dict, Any, List, Optional, Generator
from llama_stack_client import LlamaStackClient

from runtime.memory import MemoryManager
from runtime.embeddings import EmbeddingsManager
from runtime.skills import SkillsManager
from tools.core import TOOLS, execute_tool

MAX_TOOL_ROUNDS = 10


class PersonalAssistant:
    """Personal AI Assistant with memory and tool execution."""

    def __init__(
        self,
        base_url: str = "http://localhost:8321",
        model: str = "redhat-maas/qwen3-14b",
        workspace_dir: str = "workspace",
        use_embeddings: bool = True
    ):
        """
        Initialize the assistant.

        Args:
            base_url: LlamaStack server URL
            model: Model ID to use
            workspace_dir: Workspace directory for memory files
            use_embeddings: Enable vector embeddings for semantic search
        """
        self.client = LlamaStackClient(base_url=base_url)
        self.model = model
        self.workspace_dir = Path(workspace_dir)
        self.memory = MemoryManager(workspace_dir)

        # Initialize embeddings (Phase 3)
        self.embeddings: Optional[EmbeddingsManager] = None
        if use_embeddings:
            try:
                self.embeddings = EmbeddingsManager(workspace_dir)
                self.embeddings.index_workspace()
            except Exception as e:
                print(f"Warning: Could not initialize embeddings: {e}")
                print("Continuing without semantic search...")

        # Initialize skills manager
        self.skills = SkillsManager(workspace_dir)

        # Load system prompts
        self.soul = self._load_file("SOUL.md")
        self.agents_md = self._load_file("AGENTS.md")

    def _load_file(self, filename: str) -> str:
        """Load a file from workspace directory."""
        file_path = self.workspace_dir / filename
        if file_path.exists():
            return file_path.read_text()
        return ""

    def _build_system_prompt(self) -> str:
        """Build the system prompt from SOUL.md, AGENTS.md, and memory."""
        parts = []

        if self.soul:
            parts.append(self.soul)

        if self.agents_md:
            parts.append("\n\n---\n\n")
            parts.append(self.agents_md)

        # Add memory context
        memory_context = self.memory.load_context()
        if memory_context:
            parts.append("\n\n---\n\n")
            parts.append("# MEMORY AND CONTEXT\n\n")
            parts.append(memory_context)

        return "\n".join(parts)

    def _execute_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> str:
        """Execute a tool call and return the result as a string."""
        if tool_name == "memory_search" and self.embeddings:
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
            return self.skills.list_skills(tool_args.get("tag"))
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

        return execute_tool(tool_name, tool_args)

    def chat(self, user_message: str) -> str:
        """
        Send a message and get a complete response (non-streaming).
        """
        full_response = ""
        for event in self.chat_stream(user_message):
            if event["type"] == "stream_end":
                full_response = event["content"]
            elif event["type"] == "stream_error":
                full_response = f"Error: {event['error']}"
        return full_response

    def chat_stream(self, user_message: str) -> Generator[Dict[str, Any], None, None]:
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
        try:
            system_prompt = self._build_system_prompt()
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ]

            yield {"type": "stream_start"}

            all_content_parts: list[str] = []

            for _round in range(MAX_TOOL_ROUNDS):
                stream = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=TOOLS,
                    stream=True,
                )

                content_parts: list[str] = []
                tool_calls_acc: dict[int, dict] = {}
                finish_reason = None

                for chunk in stream:
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
                            result = str(self._execute_tool(tool_name, tool_args))
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
            self.memory.log_interaction(user_message, full_response)
            yield {"type": "stream_end", "content": full_response}

        except Exception as e:
            error_msg = str(e)
            self.memory.log_interaction(user_message, f"ERROR: {error_msg}")
            yield {"type": "stream_error", "error": error_msg}

    def get_memory_stats(self) -> Dict[str, Any]:
        """Get statistics about memory usage."""
        return {
            "memory_file_exists": self.memory.memory_file.exists(),
            "memory_file_size": self.memory.memory_file.stat().st_size if self.memory.memory_file.exists() else 0,
            "recent_logs": self.memory.get_recent_logs(7),
            "workspace_dir": str(self.workspace_dir.absolute())
        }
