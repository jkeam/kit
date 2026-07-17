"""
Browser automation tools using Playwright.
"""

from pathlib import Path
from typing import Optional
import base64
import subprocess
import sys


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
    script = f"""
from playwright.sync_api import sync_playwright
from pathlib import Path

output = Path('{output_path}')
output.parent.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto('{url}', wait_until='networkidle', timeout=30000)
    page.screenshot(path=str(output), full_page=True)
    browser.close()
"""

    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=60
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
    script = f"""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto('{url}', wait_until='networkidle', timeout=30000)

    title = page.title()
    url_after = page.url
    text_content = page.inner_text('body')[:1000]

    browser.close()

    print(f"Title: {{title}}")
    print(f"Final URL: {{url_after}}")
    print(f"Content: {{text_content}}")
"""

    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=60
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
    script = f"""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto('{url}', wait_until='networkidle', timeout=30000)

    elements = page.query_selector_all('{selector}')

    if not elements:
        print(f"No elements found matching selector: {selector}")
    else:
        results = []
        for i, el in enumerate(elements[:10], 1):
            text = el.inner_text().strip()
            if text:
                results.append(f"{{i}}. {{text}}")

        if results:
            for r in results:
                print(r)
        else:
            print(f"Found {{len(elements)}} elements but no text content")

    browser.close()
"""

    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=60
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
