"""Shared helpers for reading typed, defaulted configuration from
environment variables.

Several modules (gateway/server.py, runtime/skills.py, tools/core.py,
tools/web.py, tools/browser.py) each need to turn an env var like
"SHELL_EXEC_TIMEOUT_SECONDS" into an int with a safe fallback if it's
unset, blank, or not a valid number. Centralizing that here avoids
repeating (and risking drift between) the same try/except int(...)/
float(...) pattern at every call site.
"""

import os


def env_int(name: str, default: int) -> int:
    """Read an int env var, falling back to `default` if unset or invalid."""
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    """Read a float env var, falling back to `default` if unset or invalid."""
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default
