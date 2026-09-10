"""Shared helper for the fixed browser helper scripts (extract/navigate/
screenshot). Each script is invoked directly as `python <script>.py argv...`
in its own subprocess, so this relies on Python adding the script's own
directory to sys.path -- it is not meant to be imported as part of the
`tools` package.
"""
import os


def env_int(name: str, default: int) -> int:
    """Read an int env var, falling back to `default` if unset or invalid."""
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default
