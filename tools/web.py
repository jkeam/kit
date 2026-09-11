"""
Web tools for search, fetch, and browser automation.
"""

import os
from typing import Dict, Any, List
from tavily import TavilyClient
import html2text
import httpx

from env_config import env_float
from tools.net_safety import UnsafeURLError, safe_get

WEB_FETCH_TIMEOUT_SECONDS = env_float("WEB_FETCH_TIMEOUT_SECONDS", 30.0)

VALID_SEARCH_PROVIDERS = ("tavily", "brave")


def _search_tavily(query: str, max_results: int) -> str:
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return "Error: TAVILY_API_KEY not set. Get one at https://tavily.com"

    client = TavilyClient(api_key=api_key)
    response = client.search(query, max_results=max_results)

    if not response or "results" not in response:
        return "No results found"

    formatted = []
    for i, result in enumerate(response["results"], 1):
        formatted.append(
            f"{i}. **{result.get('title', 'No title')}**\n"
            f"   URL: {result.get('url', 'N/A')}\n"
            f"   {result.get('content', 'No description')}\n"
        )
    return "\n".join(formatted)


def _search_brave(query: str, max_results: int) -> str:
    api_key = os.getenv("BRAVE_API_KEY")
    if not api_key:
        return "Error: BRAVE_API_KEY not set. Get one at https://brave.com/search/api/"

    response = httpx.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": max_results},
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": api_key,
        },
        timeout=WEB_FETCH_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json()

    results = data.get("web", {}).get("results", [])
    if not results:
        return "No results found"

    formatted = []
    for i, result in enumerate(results, 1):
        formatted.append(
            f"{i}. **{result.get('title', 'No title')}**\n"
            f"   URL: {result.get('url', 'N/A')}\n"
            f"   {result.get('description', 'No description')}\n"
        )
    return "\n".join(formatted)


_SEARCH_PROVIDERS = {
    "tavily": _search_tavily,
    "brave": _search_brave,
}


def web_search(query: str, max_results: int = 5, provider: str = "") -> str:
    """
    Search the web using the specified provider.
    """
    if not provider:
        provider = os.getenv("WEB_SEARCH_PROVIDER", "tavily").lower()

    if provider not in _SEARCH_PROVIDERS:
        return f"Error: unknown search provider '{provider}'. Valid options: {', '.join(VALID_SEARCH_PROVIDERS)}"

    try:
        return _SEARCH_PROVIDERS[provider](query, max_results)
    except Exception as e:
        return f"Error searching web ({provider}): {e}"


def web_fetch(url: str, max_chars: int = 10000) -> str:
    """
    Fetch and convert web page to markdown.

    Args:
        url: URL to fetch
        max_chars: Maximum characters to return (default: 10000)

    Returns:
        Page content as markdown
    """
    try:
        # Fetch page (redirect hops are validated too - see tools/net_safety.py)
        response = safe_get(
            url,
            timeout=WEB_FETCH_TIMEOUT_SECONDS,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            }
        )
        response.raise_for_status()

        # Convert HTML to markdown
        h = html2text.HTML2Text()
        h.ignore_links = False
        h.ignore_images = True
        h.ignore_emphasis = False
        h.body_width = 0  # Don't wrap text

        markdown = h.handle(response.text)

        # Truncate if too long
        if len(markdown) > max_chars:
            markdown = markdown[:max_chars] + f"\n\n[Truncated - content was {len(markdown)} chars]"

        return f"# Content from {url}\n\n{markdown}"

    except UnsafeURLError as e:
        return f"Error: {e}"
    except httpx.HTTPError as e:
        return f"Error fetching URL: {e}"
    except Exception as e:
        return f"Error processing page: {e}"


# Tool definitions for LlamaStack
WEB_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web and return relevant results with URLs and snippets. Uses the provider set by WEB_SEARCH_PROVIDER env var (default: tavily) unless overridden.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results (default: 5)"
                    },
                    "provider": {
                        "type": "string",
                        "enum": ["tavily", "brave"],
                        "description": "Search provider to use (default: WEB_SEARCH_PROVIDER env var, or tavily)"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch and extract content from a web page as markdown",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL to fetch"
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "Maximum characters to return (default: 10000)"
                    }
                },
                "required": ["url"]
            }
        }
    }
]


# Map function names to implementations
WEB_TOOL_FUNCTIONS = {
    "web_search": web_search,
    "web_fetch": web_fetch,
}
