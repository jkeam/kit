"""
Core tools for file operations, shell execution, and memory management.
"""

import os
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Any, Dict

# Import web, browser, scheduler, and skills tools
from tools.web import WEB_TOOLS, WEB_TOOL_FUNCTIONS
from tools.browser import BROWSER_TOOLS, BROWSER_TOOL_FUNCTIONS
from tools.scheduler import SCHEDULER_TOOLS, SCHEDULER_TOOL_FUNCTIONS
from tools.skills import SKILLS_TOOLS, SKILLS_TOOL_FUNCTIONS


def read(path: str) -> str:
    """
    Read contents of a file.

    Args:
        path: File path to read

    Returns:
        File contents as string
    """
    file_path = Path(path).expanduser()

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
    file_path = Path(path).expanduser()

    try:
        # Create parent directories if they don't exist
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)
        return f"Successfully wrote to {path}"
    except Exception as e:
        return f"Error writing file: {e}"


def list_files(directory: str = ".") -> str:
    """
    List files and directories in a path.

    Args:
        directory: Directory to list (default: current directory)

    Returns:
        Formatted list of files and directories
    """
    dir_path = Path(directory).expanduser()

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


def exec_shell(command: str) -> str:
    """
    Execute a shell command.

    Args:
        command: Shell command to execute

    Returns:
        Command output (stdout + stderr)

    Security:
        - No sudo commands allowed
        - Destructive commands require confirmation in higher layer
    """
    # Safety check: block sudo
    if command.strip().startswith("sudo"):
        return "Error: sudo commands are not allowed for safety"

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,  # 30 second timeout
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
        return "Error: Command timed out (30s limit)"
    except Exception as e:
        return f"Error executing command: {e}"


def memory_write(content: str) -> str:
    """
    Append content to MEMORY.md (long-term memory).

    Args:
        content: Content to add to memory

    Returns:
        Success or error message
    """
    memory_path = Path("workspace/MEMORY.md")

    try:
        # Ensure workspace directory exists
        memory_path.parent.mkdir(parents=True, exist_ok=True)

        # Append to memory file
        with memory_path.open("a") as f:
            f.write(f"\n\n## Memory Entry - {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
            f.write(content)
            f.write("\n")

        return "Successfully added to long-term memory (MEMORY.md)"
    except Exception as e:
        return f"Error writing to memory: {e}"


def memory_get(date: str = None) -> str:
    """
    Get daily log for a specific date.

    Args:
        date: Date in YYYY-MM-DD format (default: today)

    Returns:
        Daily log content or error message
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    log_path = Path(f"workspace/memory/{date}.md")

    if not log_path.exists():
        return f"No memory log found for {date}"

    try:
        return log_path.read_text()
    except Exception as e:
        return f"Error reading daily log: {e}"


def memory_search(query: str, n_results: int = 3) -> str:
    """
    Semantic search over all memories (MEMORY.md + daily logs).

    This function requires an EmbeddingsManager instance to be passed via context.
    It's a placeholder that will be dynamically bound by the agent.

    Args:
        query: Search query
        n_results: Number of results to return

    Returns:
        Formatted search results
    """
    # This will be replaced by agent with actual embeddings manager
    return "Error: memory_search requires embeddings to be initialized"


# Core tool definitions for LlamaStack/OGX
CORE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Read the contents of a file",
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
            "description": "Write content to a file (overwrites existing)",
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
            "description": "List files and directories in a path",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "Directory to list (default: current directory)"
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

# Combine all tools
TOOLS = CORE_TOOLS + WEB_TOOLS + BROWSER_TOOLS + SCHEDULER_TOOLS + SKILLS_TOOLS


# Map function names to implementations
CORE_TOOL_FUNCTIONS = {
    "read": read,
    "write": write,
    "list_files": list_files,
    "exec_shell": exec_shell,
    "memory_write": memory_write,
    "memory_get": memory_get,
    "memory_search": memory_search,
}

# Combine all tool functions
TOOL_FUNCTIONS = {
    **CORE_TOOL_FUNCTIONS,
    **WEB_TOOL_FUNCTIONS,
    **BROWSER_TOOL_FUNCTIONS,
    **SCHEDULER_TOOL_FUNCTIONS,
    **SKILLS_TOOL_FUNCTIONS
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
