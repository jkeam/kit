"""Tests for the security-hardened tools in tools/core.py.

Assumes tests run from the repo root (so "workspace/" resolves to the real
workspace directory, matching every other relative path in this codebase).
"""

from tools.core import WORKSPACE_ROOT, exec_shell, memory_get, write


def test_memory_get_rejects_path_traversal():
    result = memory_get("../../etc/passwd")
    assert result.startswith("Error: invalid date")


def test_memory_get_rejects_malformed_date():
    assert memory_get("2026-1-1").startswith("Error: invalid date")
    assert memory_get("not-a-date").startswith("Error: invalid date")


def test_memory_get_accepts_valid_date_format():
    # No log file exists for this date, but the format itself is accepted.
    result = memory_get("2099-01-01")
    assert result == "No memory log found for 2099-01-01"


def test_write_rejects_path_outside_workspace(tmp_path):
    outside = tmp_path / "evil.txt"
    result = write(str(outside), "content")
    assert "restricted to the workspace directory" in result
    assert not outside.exists()


def test_write_allows_path_inside_workspace():
    target = WORKSPACE_ROOT / "_test_write_tmp.txt"
    try:
        result = write(str(target), "hello")
        assert "Successfully wrote" in result
        assert target.read_text() == "hello"
    finally:
        target.unlink(missing_ok=True)


def test_exec_shell_blocks_sudo():
    assert "sudo" in exec_shell("sudo ls").lower()


def test_exec_shell_blocks_shell_metacharacters():
    for command in ["ls && rm -rf /", "ls | grep x", "ls; rm -rf /", "echo `whoami`", "echo $(whoami)"]:
        result = exec_shell(command)
        assert "metacharacters" in result, f"expected rejection for: {command}"


def test_exec_shell_runs_simple_command():
    result = exec_shell("echo hello-from-test")
    assert "hello-from-test" in result
