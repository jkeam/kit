"""
Web tools for search, fetch, and browser automation.
"""

import os
from typing import Dict, Any, List
from tavily import TavilyClient
import html2text
import httpx


def web_search(query: str, max_results: int = 5) -> str:
    """
    Search the web using Tavily API.

    Args:
        query: Search query
        max_results: Maximum number of results (default: 5)

    Returns:
        Formatted search results with titles, URLs, and snippets
    """
    api_key = os.getenv("TAVILY_API_KEY")

    if not api_key:
        return "Error: TAVILY_API_KEY not set. Get one at https://tavily.com"

    try:
        client = TavilyClient(api_key=api_key)
        response = client.search(query, max_results=max_results)

        if not response or "results" not in response:
            return "No results found"

        # Format results
        formatted = []
        for i, result in enumerate(response["results"], 1):
            formatted.append(
                f"{i}. **{result.get('title', 'No title')}**\n"
                f"   URL: {result.get('url', 'N/A')}\n"
                f"   {result.get('content', 'No description')}\n"
            )

        return "\n".join(formatted)

    except Exception as e:
        return f"Error searching web: {e}"


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
        # Fetch page
        response = httpx.get(
            url,
            follow_redirects=True,
            timeout=30.0,
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
            "description": "Search the web and return relevant results with URLs and snippets",
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
