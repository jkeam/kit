"""
Browser automation tools using Playwright.
"""

from pathlib import Path
from typing import Optional
import subprocess
import sys

from env_config import env_int

# Fixed helper scripts, invoked with untrusted values passed as real argv
# entries (never interpolated into Python source) to avoid code injection.
_SCRIPTS_DIR = Path(__file__).parent / "_browser_scripts"

# Subprocess wall-clock budget; the scripts themselves read
# BROWSER_NAV_TIMEOUT_MS (inherited from this process's env) for the
# Playwright page.goto timeout, which should stay comfortably under this.
BROWSER_SUBPROCESS_TIMEOUT_SECONDS = env_int("BROWSER_SUBPROCESS_TIMEOUT_SECONDS", 60)


def browser_screenshot(url: str, output_path: str = "workspace/screenshot.png") -> str:
    """
    Take a screenshot of a web page.

    Args:
        url: URL to screenshot
        output_path: Path to save screenshot (default: workspace/screenshot.png)

    Returns:
        Success message with path or error
    """
    # Run in subprocess to avoid asyncio conflicts
    try:
        result = subprocess.run(
            [sys.executable, str(_SCRIPTS_DIR / "screenshot.py"), url, output_path],
            capture_output=True,
            text=True,
            timeout=BROWSER_SUBPROCESS_TIMEOUT_SECONDS
        )

        if result.returncode != 0:
            return f"Error taking screenshot: {result.stderr}"

        return f"Screenshot saved to {output_path}"

    except Exception as e:
        return f"Error taking screenshot: {e}"


def browser_navigate(url: str, actions: str) -> str:
    """
    Navigate a browser and perform actions.

    Args:
        url: Starting URL
        actions: Description of actions to perform (e.g., "click button with text 'Submit'")

    Returns:
        Result of navigation
    """
    try:
        result = subprocess.run(
            [sys.executable, str(_SCRIPTS_DIR / "navigate.py"), url],
            capture_output=True,
            text=True,
            timeout=BROWSER_SUBPROCESS_TIMEOUT_SECONDS
        )

        if result.returncode != 0:
            return f"Error navigating: {result.stderr}"

        return (
            f"Navigation result:\n{result.stdout}\n\n"
            f"Note: Advanced actions ('{actions}') require manual implementation"
        )

    except Exception as e:
        return f"Error navigating: {e}"


def browser_extract(url: str, selector: str) -> str:
    """
    Extract content from web page using CSS selector.

    Args:
        url: URL to extract from
        selector: CSS selector (e.g., "h1", ".classname", "#id")

    Returns:
        Extracted text content
    """
    try:
        result = subprocess.run(
            [sys.executable, str(_SCRIPTS_DIR / "extract.py"), url, selector],
            capture_output=True,
            text=True,
            timeout=BROWSER_SUBPROCESS_TIMEOUT_SECONDS
        )

        if result.returncode != 0:
            return f"Error extracting content: {result.stderr}"

        return result.stdout.strip() if result.stdout else "No content extracted"

    except Exception as e:
        return f"Error extracting content: {e}"


# Tool definitions
BROWSER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "browser_screenshot",
            "description": "Take a screenshot of a web page and save it",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL to screenshot"
                    },
                    "output_path": {
                        "type": "string",
                        "description": "Path to save screenshot (default: workspace/screenshot.png)"
                    }
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_navigate",
            "description": "Navigate to a URL and interact with the page (basic actions)",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Starting URL"
                    },
                    "actions": {
                        "type": "string",
                        "description": "Description of actions to perform"
                    }
                },
                "required": ["url", "actions"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_extract",
            "description": "Extract text from web page using CSS selector",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL to extract from"
                    },
                    "selector": {
                        "type": "string",
                        "description": "CSS selector (e.g., 'h1', '.class', '#id')"
                    }
                },
                "required": ["url", "selector"]
            }
        }
    }
]


# Function map
BROWSER_TOOL_FUNCTIONS = {
    "browser_screenshot": browser_screenshot,
    "browser_navigate": browser_navigate,
    "browser_extract": browser_extract,
}
