"""
MCP (Model Context Protocol) client manager.

Each agent session gets its own MCPManager, which starts the MCP server
subprocesses declared in that agent's definition, discovers their tools,
and routes tool calls back to the correct server.
"""

import os
import re
from contextlib import AsyncExitStack
from typing import Any, Dict, List, Optional, Tuple

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

MCP_TOOL_PREFIX = "mcp__"
MCP_TOOL_SEP = "__"


def _expand_env(env: Dict[str, str]) -> Dict[str, str]:
    """Expand ${VAR} references in env values against os.environ."""
    def _replace(match):
        return os.environ.get(match.group(1), match.group(0))
    return {
        k: re.sub(r"\$\{(\w+)\}", _replace, v) if isinstance(v, str) else v
        for k, v in env.items()
    }


def make_mcp_tool_name(server_name: str, tool_name: str) -> str:
    return f"{MCP_TOOL_PREFIX}{server_name}{MCP_TOOL_SEP}{tool_name}"


def parse_mcp_tool_name(namespaced: str) -> Optional[Tuple[str, str]]:
    """Parse 'mcp__server__tool' -> ('server', 'tool'), or None."""
    if not namespaced.startswith(MCP_TOOL_PREFIX):
        return None
    rest = namespaced[len(MCP_TOOL_PREFIX):]
    sep_idx = rest.find(MCP_TOOL_SEP)
    if sep_idx < 0:
        return None
    return rest[:sep_idx], rest[sep_idx + len(MCP_TOOL_SEP):]


class _MCPConnection:
    """A live connection to a single MCP server."""

    __slots__ = ("name", "session", "tools")

    def __init__(self, name: str, session: ClientSession, tools: list):
        self.name = name
        self.session = session
        self.tools = tools


class MCPManager:
    """Manages MCP server connections for a single agent session.

    Lifecycle:
        mgr = MCPManager(configs)
        await mgr.connect()       # start servers, discover tools
        tools = mgr.get_openai_tools()  # merge into agent's tool list
        result = await mgr.call_tool("server", "tool", {...})
        await mgr.close()         # shut down servers
    """

    def __init__(self, server_configs: Dict[str, dict]):
        self.server_configs = server_configs
        self._connections: Dict[str, _MCPConnection] = {}
        self._exit_stack: Optional[AsyncExitStack] = None
        self._connected = False

    async def connect(self) -> None:
        if self._connected:
            return

        self._exit_stack = AsyncExitStack()
        await self._exit_stack.__aenter__()

        for name, config in self.server_configs.items():
            try:
                await self._connect_server(name, config)
            except Exception as e:
                print(f"Warning: MCP server '{name}' failed to connect: {e}")

        self._connected = True

    async def _connect_server(self, name: str, config: dict) -> None:
        if "command" not in config:
            raise ValueError(
                f"MCP server '{name}' config requires 'command' "
                "(stdio transport); URL-based transport is not yet supported"
            )

        env = {**os.environ, **_expand_env(config.get("env") or {})}
        params = StdioServerParameters(
            command=config["command"],
            args=config.get("args", []),
            env=env,
        )

        read_stream, write_stream = await self._exit_stack.enter_async_context(
            stdio_client(params)
        )
        session = await self._exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await session.initialize()

        result = await session.list_tools()
        self._connections[name] = _MCPConnection(
            name=name, session=session, tools=result.tools
        )

    def get_openai_tools(self) -> List[dict]:
        """All connected servers' tools in OpenAI function-calling format."""
        tools = []
        for conn in self._connections.values():
            for tool in conn.tools:
                schema = tool.inputSchema or {"type": "object", "properties": {}}
                tools.append({
                    "type": "function",
                    "function": {
                        "name": make_mcp_tool_name(conn.name, tool.name),
                        "description": tool.description or "",
                        "parameters": schema,
                    },
                })
        return tools

    async def call_tool(
        self, server_name: str, tool_name: str, arguments: Dict[str, Any]
    ) -> str:
        conn = self._connections.get(server_name)
        if not conn:
            return f"Error: MCP server '{server_name}' is not connected"

        try:
            result = await conn.session.call_tool(tool_name, arguments)
            parts = []
            for block in result.content:
                if hasattr(block, "text"):
                    parts.append(block.text)
                else:
                    parts.append(str(block))
            return "\n".join(parts) if parts else "Tool completed (no output)"
        except Exception as e:
            return f"Error calling MCP tool '{tool_name}' on '{server_name}': {e}"

    async def close(self) -> None:
        if self._exit_stack:
            try:
                await self._exit_stack.aclose()
            except Exception:
                pass
            self._connections.clear()
            self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected
