"""
Agent Runtime - orchestrates LLM calls, tool execution, and memory.

This integrates with LlamaStack (soon OGX) which handles the ReAct loop.
"""

import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from llama_stack_client import LlamaStackClient

from runtime.memory import MemoryManager
from runtime.embeddings import EmbeddingsManager
from runtime.skills import SkillsManager
from tools.core import TOOLS, execute_tool


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

    def chat(self, user_message: str) -> str:
        """
        Send a message to the assistant and get a response.

        Args:
            user_message: The user's message

        Returns:
            The assistant's response
        """
        try:
            # Build system prompt with memory
            system_prompt = self._build_system_prompt()

            # Create messages (OpenAI format)
            messages = [
                {
                    "role": "system",
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": user_message
                }
            ]

            # Make request to LlamaStack
            # LlamaStack will handle the ReAct loop (tool calling + execution)
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=TOOLS,
                # Note: In a real implementation, we'd set up a tool executor
                # For now, we'll handle tools in a simpler way
            )

            # Extract response
            if response.choices and len(response.choices) > 0:
                choice = response.choices[0]
                assistant_message = choice.message.content or ""

                # Check if there are tool calls
                if hasattr(choice.message, 'tool_calls') and choice.message.tool_calls:
                    # Handle tool calls
                    tool_results = []
                    for tool_call in choice.message.tool_calls:
                        tool_name = tool_call.function.name
                        try:
                            tool_args = json.loads(tool_call.function.arguments)

                            # Special handling for memory_search
                            if tool_name == "memory_search" and self.embeddings:
                                query = tool_args.get("query", "")
                                n_results = tool_args.get("n_results", 3)
                                search_results = self.embeddings.search(query, n_results)

                                # Format results
                                formatted = []
                                for r in search_results:
                                    formatted.append(
                                        f"[{r['source_type']}] {r['content'][:200]}... "
                                        f"(from {Path(r['source']).name})"
                                    )
                                result = "\n\n".join(formatted) if formatted else "No relevant memories found"

                            # Special handling for skills tools
                            elif tool_name.startswith("skill_"):
                                if tool_name == "skill_create":
                                    params_str = tool_args.get("parameters")
                                    params = json.loads(params_str) if params_str else None
                                    tags = tool_args.get("tags", "").split(",") if tool_args.get("tags") else None
                                    result = self.skills.create_skill(
                                        name=tool_args["name"],
                                        description=tool_args["description"],
                                        code=tool_args["code"],
                                        parameters=params,
                                        tags=tags
                                    )
                                elif tool_name == "skill_list":
                                    result = self.skills.list_skills(tool_args.get("tag"))
                                elif tool_name == "skill_execute":
                                    args_str = tool_args.get("args")
                                    kwargs = json.loads(args_str) if args_str else {}
                                    result = self.skills.execute_skill(tool_args["name"], **kwargs)
                                elif tool_name == "skill_improve":
                                    result = self.skills.improve_skill(
                                        name=tool_args["name"],
                                        changes=tool_args["changes"],
                                        code=tool_args.get("code")
                                    )
                                elif tool_name == "skill_delete":
                                    result = self.skills.delete_skill(tool_args["name"])
                                elif tool_name == "skill_info":
                                    info = self.skills.get_skill_info(tool_args["name"])
                                    result = json.dumps(info, indent=2) if info else f"Skill '{tool_args['name']}' not found"
                                else:
                                    result = execute_tool(tool_name, tool_args)

                            else:
                                result = execute_tool(tool_name, tool_args)

                            tool_results.append(f"Tool '{tool_name}' result: {result}")
                        except Exception as e:
                            tool_results.append(f"Tool '{tool_name}' error: {e}")

                    # For Phase 1, we'll include tool results in response
                    # In Phase 2+, we'd feed this back to the model
                    if tool_results:
                        assistant_message += "\n\n" + "\n".join(tool_results)

                # Log interaction to daily memory
                self.memory.log_interaction(user_message, assistant_message)

                return assistant_message
            else:
                error_msg = "No response from model"
                self.memory.log_interaction(user_message, f"ERROR: {error_msg}")
                return error_msg

        except Exception as e:
            error_msg = f"Error: {str(e)}"
            self.memory.log_interaction(user_message, f"ERROR: {error_msg}")
            return error_msg

    def get_memory_stats(self) -> Dict[str, Any]:
        """Get statistics about memory usage."""
        return {
            "memory_file_exists": self.memory.memory_file.exists(),
            "memory_file_size": self.memory.memory_file.stat().st_size if self.memory.memory_file.exists() else 0,
            "recent_logs": self.memory.get_recent_logs(7),
            "workspace_dir": str(self.workspace_dir.absolute())
        }
