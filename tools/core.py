"""
Core tools for file operations, shell execution, and memory management.
"""

import os
import re
import shlex
import subprocess
import yaml
from pathlib import Path
from datetime import datetime
from typing import Any, Dict

# Import web, browser, scheduler, and skills tools
from tools.web import WEB_TOOLS, WEB_TOOL_FUNCTIONS
from tools.browser import BROWSER_TOOLS, BROWSER_TOOL_FUNCTIONS
from tools.scheduler import SCHEDULER_TOOLS, SCHEDULER_TOOL_FUNCTIONS
from tools.skills import SKILLS_TOOLS, SKILLS_TOOL_FUNCTIONS
from tools.delegation import DELEGATION_TOOLS, DELEGATION_TOOL_FUNCTIONS
from tools.knowledge import KNOWLEDGE_TOOLS, KNOWLEDGE_TOOL_FUNCTIONS
from env_config import env_int

WORKSPACE_ROOT = Path("workspace").resolve()
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SHELL_METACHARACTERS_RE = re.compile(r'[&|;`$<>]')
SHELL_EXEC_TIMEOUT_SECONDS = env_int("SHELL_EXEC_TIMEOUT_SECONDS", 30)

# Blocking metacharacters stops a single exec_shell call from chaining into
# extra commands, but it can't stop one destructive command run on its own
# (e.g. `rm -rf ~`). Block the highest-risk commands by default; config.yaml
# can override this list (set tools.safety.blocked_commands: [] to disable).
DEFAULT_BLOCKED_COMMANDS = [
    "rm", "mv", "dd", "mkfs", "shutdown", "reboot", "halt",
    "chmod", "chown", "kill", "killall", "pkill",
]


def read(path: str) -> str:
    """
    Read contents of a file.

    Args:
        path: File path to read

    Returns:
        File contents as string

    Security:
        Restricted to the workspace directory (same boundary as write()) —
        without this, a prompt-injected LLM could read /etc/shadow, SSH
        keys, .env files, or the gateway token.
    """
    file_path = Path(path).expanduser().resolve()

    if not file_path.is_relative_to(WORKSPACE_ROOT):
        return f"Error: reads are restricted to the workspace directory ({WORKSPACE_ROOT})"

    if not file_path.exists():
        return f"Error: File not found: {path}"

    if not file_path.is_file():
        return f"Error: Path is not a file: {path}"

    try:
        return file_path.read_text()
    except Exception as e:
        return f"Error reading file: {e}"


def write(path: str, content: str) -> str:
    """
    Write content to a file (overwrites existing).

    Args:
        path: File path to write
        content: Content to write

    Returns:
        Success or error message
    """
    file_path = Path(path).expanduser().resolve()

    if not file_path.is_relative_to(WORKSPACE_ROOT):
        return f"Error: writes are restricted to the workspace directory ({WORKSPACE_ROOT})"

    try:
        # Create parent directories if they don't exist
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)
        return f"Successfully wrote to {path}"
    except Exception as e:
        return f"Error writing file: {e}"


def list_files(directory: str = "workspace") -> str:
    """
    List files and directories in a path.

    Args:
        directory: Directory to list (default: the workspace directory)

    Returns:
        Formatted list of files and directories

    Security:
        Restricted to the workspace directory (same boundary as read()/
        write()) to prevent filesystem reconnaissance.
    """
    dir_path = Path(directory).expanduser().resolve()

    if not dir_path.is_relative_to(WORKSPACE_ROOT):
        return f"Error: listing is restricted to the workspace directory ({WORKSPACE_ROOT})"

    if not dir_path.exists():
        return f"Error: Directory not found: {directory}"

    if not dir_path.is_dir():
        return f"Error: Path is not a directory: {directory}"

    try:
        items = []
        for item in sorted(dir_path.iterdir()):
            if item.is_dir():
                items.append(f"📁 {item.name}/")
            else:
                size = item.stat().st_size
                items.append(f"📄 {item.name} ({size:,} bytes)")

        if not items:
            return f"Directory is empty: {directory}"

        return "\n".join(items)
    except Exception as e:
        return f"Error listing directory: {e}"


def _load_shell_safety_config() -> Dict[str, Any]:
    """Load the `tools.safety` block from config.yaml, if present."""
    config_path = Path("config.yaml")
    if not config_path.exists():
        return {}
    try:
        config = yaml.safe_load(config_path.read_text()) or {}
    except Exception:
        return {}
    return (config.get("tools") or {}).get("safety") or {}


def exec_shell(command: str) -> str:
    """
    Execute a shell command.

    Args:
        command: Shell command to execute

    Returns:
        Command output (stdout + stderr)

    Security:
        - No sudo commands allowed
        - No shell metacharacters (pipes, chains, redirects, substitution)
        - Runs with shell=False; if config.yaml sets tools.safety.allowed_commands,
          the command's first token must be in that list
        - Otherwise, the command's first token is checked against
          tools.safety.blocked_commands (defaults to DEFAULT_BLOCKED_COMMANDS)
          to stop single-shot destructive commands like `rm -rf`
        - Destructive commands require confirmation in higher layer
    """
    # Safety check: block sudo
    if command.strip().startswith("sudo"):
        return "Error: sudo commands are not allowed for safety"

    # Safety check: block shell metacharacters that would let a single
    # exec_shell call chain/redirect into arbitrary additional commands
    if SHELL_METACHARACTERS_RE.search(command):
        return "Error: command contains disallowed shell metacharacters (& | ; ` $ < >)"

    try:
        args = shlex.split(command)
    except ValueError as e:
        return f"Error: could not parse command: {e}"

    if not args:
        return "Error: empty command"

    safety = _load_shell_safety_config()
    allowed_commands = safety.get("allowed_commands") or []
    if allowed_commands:
        if args[0] not in allowed_commands:
            return f"Error: command '{args[0]}' is not in the allowed_commands list"
    else:
        blocked_commands = safety.get("blocked_commands", DEFAULT_BLOCKED_COMMANDS)
        if args[0] in blocked_commands:
            return f"Error: command '{args[0]}' is blocked for safety (set tools.safety.allowed_commands or blocked_commands in config.yaml to change this)"

    try:
        result = subprocess.run(
            args,
            shell=False,
            capture_output=True,
            text=True,
            timeout=SHELL_EXEC_TIMEOUT_SECONDS,
            cwd=os.getcwd()
        )

        output = []
        if result.stdout:
            output.append("STDOUT:")
            output.append(result.stdout)
        if result.stderr:
            output.append("STDERR:")
            output.append(result.stderr)
        if result.returncode != 0:
            output.append(f"\nExit code: {result.returncode}")

        return "\n".join(output) if output else "Command completed (no output)"

    except subprocess.TimeoutExpired:
        return f"Error: Command timed out ({SHELL_EXEC_TIMEOUT_SECONDS}s limit)"
    except Exception as e:
        return f"Error executing command: {e}"


def memory_write(content: str, agent_id: str = "kit") -> str:
    """
    Append content to this agent's MEMORY.md (long-term memory).

    Args:
        content: Content to add to memory
        agent_id: The agent whose memory to write to

    Returns:
        Success or error message
    """
    memory_path = Path(f"workspace/memory/{agent_id}/MEMORY.md")

    try:
        memory_path.parent.mkdir(parents=True, exist_ok=True)

        with memory_path.open("a") as f:
            f.write(f"\n\n## Memory Entry - {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
            f.write(content)
            f.write("\n")

        return "Successfully added to long-term memory (MEMORY.md)"
    except Exception as e:
        return f"Error writing to memory: {e}"


def memory_get(date: str = None, agent_id: str = "kit") -> str:
    """
    Get this agent's daily log for a specific date.

    Args:
        date: Date in YYYY-MM-DD format (default: today)
        agent_id: The agent whose log to read

    Returns:
        Daily log content or error message
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    elif not DATE_RE.match(date):
        return f"Error: invalid date '{date}', expected YYYY-MM-DD format"

    log_path = Path(f"workspace/memory/{agent_id}/{date}.md")

    if not log_path.exists():
        return f"No memory log found for {date}"

    try:
        return log_path.read_text()
    except Exception as e:
        return f"Error reading daily log: {e}"


# Note: "memory_search" has a tool schema below (so the LLM knows it exists)
# but no entry in CORE_TOOL_FUNCTIONS. It's only ever dispatched through
# PersonalAssistant._execute_tool() in runtime/agent.py, which has access to
# the EmbeddingsManager needed to actually perform the search.

# Core tool definitions for LlamaStack/OGX
CORE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Read the contents of a file. Restricted to the workspace directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to read"
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write",
            "description": "Write content to a file (overwrites existing). Restricted to the workspace directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to write"
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write to the file"
                    }
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files and directories in a path. Restricted to the workspace directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "Directory to list (default: the workspace directory)"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "exec_shell",
            "description": "Execute a shell command. No sudo allowed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute"
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "memory_write",
            "description": "Add important information to long-term memory (MEMORY.md)",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Content to add to memory"
                    }
                },
                "required": ["content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "memory_get",
            "description": "Retrieve daily memory log for a specific date",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Date in YYYY-MM-DD format (default: today)"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "memory_search",
            "description": "Semantic search over all memories to find relevant information",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (e.g., 'Python preferences', 'recent decisions')"
                    },
                    "n_results": {
                        "type": "integer",
                        "description": "Number of results to return (default: 3)"
                    }
                }
            }
        }
    }
]

# Combine all tools. agent_delegate is opt-in per agent (see
# runtime/agent.py's allowed_tools filtering) - being in this global list
# doesn't hand it to every agent, only to whichever ones have it in their
# own configured tool allowlist (Kit does, by default).
TOOLS = CORE_TOOLS + WEB_TOOLS + BROWSER_TOOLS + SCHEDULER_TOOLS + SKILLS_TOOLS + DELEGATION_TOOLS + KNOWLEDGE_TOOLS


# Map function names to implementations
CORE_TOOL_FUNCTIONS = {
    "read": read,
    "write": write,
    "list_files": list_files,
    "exec_shell": exec_shell,
    "memory_write": memory_write,
    "memory_get": memory_get,
}

# Combine all tool functions
TOOL_FUNCTIONS = {
    **CORE_TOOL_FUNCTIONS,
    **WEB_TOOL_FUNCTIONS,
    **BROWSER_TOOL_FUNCTIONS,
    **SCHEDULER_TOOL_FUNCTIONS,
    **SKILLS_TOOL_FUNCTIONS,
    **DELEGATION_TOOL_FUNCTIONS,
    **KNOWLEDGE_TOOL_FUNCTIONS,
}


def execute_tool(name: str, arguments: Dict[str, Any]) -> str:
    """
    Execute a tool by name with given arguments.

    Args:
        name: Tool function name
        arguments: Dictionary of arguments

    Returns:
        Tool execution result
    """
    if name not in TOOL_FUNCTIONS:
        return f"Error: Unknown tool '{name}'"

    try:
        func = TOOL_FUNCTIONS[name]
        result = func(**arguments)
        return result
    except TypeError as e:
        return f"Error: Invalid arguments for tool '{name}': {e}"
    except Exception as e:
        return f"Error executing tool '{name}': {e}"
