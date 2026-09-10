"""Tests for per-agent tool/skill scoping on PersonalAssistant
(runtime/agent.py) - the mechanism that lets a non-Kit team member see only
a curated subset of tools/skills, enforced both in the schema list sent to
the LLM and at dispatch time.
"""

import pytest

from runtime.agent import PersonalAssistant


@pytest.fixture
def unrestricted_agent(tmp_path):
    return PersonalAssistant(workspace_dir=str(tmp_path), use_embeddings=False)


@pytest.fixture
def restricted_agent(tmp_path):
    return PersonalAssistant(
        workspace_dir=str(tmp_path),
        use_embeddings=False,
        allowed_tools={"read", "list_files"},
        allowed_skills={"count-python-files"},
        soul_override="# Tester\nYou run tests.",
    )


def test_unrestricted_agent_gets_full_tool_list(unrestricted_agent):
    from tools.core import TOOLS
    assert len(unrestricted_agent._filtered_tools) == len(TOOLS)


def test_restricted_agent_gets_only_allowlisted_tools(restricted_agent):
    names = {t["function"]["name"] for t in restricted_agent._filtered_tools}
    assert names == {"read", "list_files"}


def test_restricted_agent_rejects_disallowed_tool_call(restricted_agent):
    result = restricted_agent._execute_tool("exec_shell", {"command": "echo hi"})
    assert "not available to this agent" in result


def test_restricted_agent_allows_allowlisted_tool_call(restricted_agent, tmp_path):
    (tmp_path / "hello.txt").write_text("hi")
    result = restricted_agent._execute_tool("read", {"path": "hello.txt"})
    assert "not available to this agent" not in result


def test_restricted_agent_rejects_disallowed_skill(restricted_agent):
    result = restricted_agent._execute_tool(
        "skill_execute", {"name": "some-other-skill"}
    )
    assert "not available to this agent" in result


def test_soul_override_replaces_workspace_soul(restricted_agent):
    assert restricted_agent.soul == "# Tester\nYou run tests."


def test_default_soul_loads_workspace_file(tmp_path):
    (tmp_path / "SOUL.md").write_text("# Kit\nYou are Kit.")
    agent = PersonalAssistant(workspace_dir=str(tmp_path), use_embeddings=False)
    assert agent.soul == "# Kit\nYou are Kit."
