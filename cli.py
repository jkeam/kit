#!/usr/bin/env python3
"""
Command-line interface for the Personal Assistant (Gateway version).

This version communicates with the gateway server instead of calling the agent directly.

Usage:
    python cli.py "Your message here"
    python cli.py --stats
"""

import os
import sys
import json
import httpx
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.live import Live


GATEWAY_URL = "http://localhost:18789"
PLATFORM = "cli"
USER_ID = "local"  # Single user for CLI

# Matches gateway/server.py's _require_gateway_token: if the gateway has
# GATEWAY_TOKEN set, requests need this to authenticate.
_AUTH_HEADERS = (
    {"Authorization": f"Bearer {os.environ['GATEWAY_TOKEN']}"}
    if os.environ.get("GATEWAY_TOKEN")
    else {}
)


def main():
    console = Console()

    # Parse arguments
    if len(sys.argv) < 2:
        console.print("[red]Error:[/red] Please provide a message")
        console.print("\n[bold]Usage:[/bold]")
        console.print("  python cli.py \"Your message here\"")
        console.print("  python cli.py --stats")
        sys.exit(1)

    arg = sys.argv[1]

    # Handle --stats
    if arg == "--stats":
        try:
            response = httpx.get(f"{GATEWAY_URL}/sessions", headers=_AUTH_HEADERS)
            response.raise_for_status()
            sessions = response.json()

            console.print("\n[bold cyan]Active Sessions[/bold cyan]\n")
            for session in sessions:
                console.print(f"[bold]{session['session_id']}[/bold]")
                console.print(f"  Platform: {session['platform']}")
                console.print(f"  Messages: {session['message_count']}")
                console.print(f"  Last active: {session['last_active']}")
                console.print()

            if not sessions:
                console.print("[dim]No active sessions[/dim]")

        except httpx.ConnectError:
            console.print("[red]Error:[/red] Cannot connect to gateway server")
            console.print("\n[yellow]Make sure the gateway is running:[/yellow]")
            console.print("  python -m gateway.server")
            sys.exit(1)
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            sys.exit(1)
        return

    # Get user message
    user_message = arg

    # Display user message
    console.print("\n")
    console.print(Panel(
        user_message,
        title="[bold cyan]You[/bold cyan]",
        border_style="cyan"
    ))
    console.print()

    # Stream from gateway
    try:
        streaming_text = ""
        message_count = 0

        with httpx.Client(timeout=None) as client:
            with client.stream(
                "POST",
                f"{GATEWAY_URL}/chat/stream",
                json={
                    "platform": PLATFORM,
                    "user_id": USER_ID,
                    "message": user_message,
                },
                headers=_AUTH_HEADERS,
            ) as response:
                response.raise_for_status()

                with Live(console=console, refresh_per_second=10) as live:
                    for line in response.iter_lines():
                        if not line.startswith("data: "):
                            continue
                        event = json.loads(line[6:])
                        etype = event.get("type")

                        if etype == "text_delta":
                            streaming_text += event["content"]
                            live.update(Panel(
                                Markdown(streaming_text),
                                title="[bold green]Assistant[/bold green]",
                                border_style="green",
                            ))

                        elif etype == "tool_call_start":
                            live.console.print(
                                f"  [dim]▶ {event['tool_name']}[/dim]"
                            )

                        elif etype == "tool_call_result":
                            live.console.print(
                                f"  [dim]✓ {event['tool_name']}[/dim]"
                            )

                        elif etype == "stream_end":
                            message_count = event.get("message_count", 0)

                        elif etype == "stream_error":
                            console.print(
                                f"\n[red]Error:[/red] {event.get('error')}"
                            )
                            sys.exit(1)

        # Final render with message count
        if streaming_text:
            console.print(Panel(
                Markdown(streaming_text),
                title=f"[bold green]Assistant[/bold green] [dim](msg #{message_count})[/dim]",
                border_style="green",
            ))
        console.print()

    except httpx.ConnectError:
        console.print("[red]Error:[/red] Cannot connect to gateway server")
        console.print("\n[yellow]Make sure the gateway is running:[/yellow]")
        console.print("  python -m gateway.server")
        sys.exit(1)
    except KeyboardInterrupt:
        console.print("\n\n[yellow]Interrupted by user[/yellow]")
        sys.exit(0)
    except Exception as e:
        console.print(f"\n[red]Error:[/red] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
