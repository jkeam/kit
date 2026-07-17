#!/usr/bin/env python3
"""
Command-line interface for the Personal Assistant (Gateway version).

This version communicates with the gateway server instead of calling the agent directly.

Usage:
    python cli_v2.py "Your message here"
    python cli_v2.py --stats
"""

import sys
import httpx
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown


GATEWAY_URL = "http://localhost:18789"
PLATFORM = "cli"
USER_ID = "local"  # Single user for CLI


def main():
    console = Console()

    # Parse arguments
    if len(sys.argv) < 2:
        console.print("[red]Error:[/red] Please provide a message")
        console.print("\n[bold]Usage:[/bold]")
        console.print("  python cli_v2.py \"Your message here\"")
        console.print("  python cli_v2.py --stats")
        sys.exit(1)

    arg = sys.argv[1]

    # Handle --stats
    if arg == "--stats":
        try:
            response = httpx.get(f"{GATEWAY_URL}/sessions")
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

    # Send to gateway
    console.print("\n[dim]Thinking...[/dim]\n")

    try:
        response = httpx.post(
            f"{GATEWAY_URL}/chat",
            json={
                "platform": PLATFORM,
                "user_id": USER_ID,
                "message": user_message
            },
            timeout=30.0
        )
        response.raise_for_status()
        data = response.json()

        # Display response
        console.print(Panel(
            Markdown(data["response"]),
            title=f"[bold green]Assistant[/bold green] [dim](msg #{data['message_count']})[/dim]",
            border_style="green"
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
