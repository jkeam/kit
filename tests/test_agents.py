"""Tests for the agent registry (runtime/agents.py): template loading/
overrides, agent CRUD, the built-in "kit" agent, and tool/skill validation.
"""

import json

import pytest

from runtime.agents import KIT_AGENT_ID, AgentRegistry


@pytest.fixture
def registry(tmp_path):
    workspace = tmp_path / "workspace"
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "tester.json").write_text(json.dumps({
        "id": "tester",
        "name": "Tester",
        "description": "Runs tests.",
        "tools": ["read", "list_files"],
        "skills": [],
        "soul": "# Tester\nYou run tests.",
    }))
    return AgentRegistry(workspace_dir=str(workspace), templates_dir=str(templates))


def test_kit_is_synthesized_with_full_access(registry):
    kit = registry.resolve(KIT_AGENT_ID)
    assert kit is not None
    assert kit.allowed_tools is None
    assert kit.allowed_skills is None


def test_kit_persona_reads_workspace_soul_md(registry):
    (registry.workspace_dir / "SOUL.md").parent.mkdir(parents=True, exist_ok=True)
    (registry.workspace_dir / "SOUL.md").write_text("# Kit\nYou are Kit.")
    kit = registry.resolve(KIT_AGENT_ID)
    assert kit.soul == "# Kit\nYou are Kit."


def test_list_agents_always_includes_kit_first(registry):
    agents = registry.list_agents()
    assert agents[0].id == KIT_AGENT_ID


def test_list_templates_includes_built_in(registry):
    templates = registry.list_templates()
    ids = {t["id"] for t in templates}
    assert "tester" in ids


def test_user_template_overrides_built_in_by_id(registry):
    registry.save_template({
        "id": "tester",
        "name": "Tester (custom)",
        "description": "Custom tester.",
        "tools": ["read"],
        "skills": [],
        "soul": "# Custom Tester",
    })
    template = registry.get_template("tester")
    assert template["name"] == "Tester (custom)"


def test_create_agent_from_template(registry):
    agent = registry.create_agent(template_id="tester", id="tester-1")
    assert agent.id == "tester-1"
    assert agent.allowed_tools == {"read", "list_files"}
    assert agent.soul == "# Tester\nYou run tests."


def test_create_agent_with_overrides(registry):
    agent = registry.create_agent(
        template_id="tester",
        id="tester-1",
        tool_overrides=["read"],
        soul_overrides="# Custom",
    )
    assert agent.allowed_tools == {"read"}
    assert agent.soul == "# Custom"


def test_create_agent_rejects_kit_id(registry):
    with pytest.raises(ValueError):
        registry.create_agent(template_id="tester", id=KIT_AGENT_ID)


def test_create_agent_rejects_duplicate_id(registry):
    registry.create_agent(template_id="tester", id="tester-1")
    with pytest.raises(ValueError):
        registry.create_agent(template_id="tester", id="tester-1")


def test_create_agent_rejects_unknown_tool(registry):
    with pytest.raises(ValueError):
        registry.create_agent(template_id="tester", id="tester-1", tool_overrides=["not_a_real_tool"])


def test_create_agent_rejects_unknown_template(registry):
    with pytest.raises(ValueError):
        registry.create_agent(template_id="does-not-exist", id="tester-1")


def test_update_agent(registry):
    registry.create_agent(template_id="tester", id="tester-1")
    updated = registry.update_agent("tester-1", tools=["read", "list_files", "exec_shell"])
    assert updated.allowed_tools == {"read", "list_files", "exec_shell"}


def test_update_agent_rejects_kit(registry):
    with pytest.raises(ValueError):
        registry.update_agent(KIT_AGENT_ID, tools=["read"])


def test_delete_agent(registry):
    registry.create_agent(template_id="tester", id="tester-1")
    assert registry.delete_agent("tester-1") is True
    assert registry.resolve("tester-1") is None


def test_delete_agent_rejects_kit(registry):
    with pytest.raises(ValueError):
        registry.delete_agent(KIT_AGENT_ID)


def test_delete_nonexistent_agent_returns_false(registry):
    assert registry.delete_agent("does-not-exist") is False


def test_wildcard_tools_and_skills_skip_validation(registry):
    registry.save_template({
        "id": "full-access",
        "name": "Full Access",
        "description": "",
        "tools": "*",
        "skills": "*",
        "soul": "",
    })
    agent = registry.create_agent(template_id="full-access", id="full-1")
    assert agent.allowed_tools is None
    assert agent.allowed_skills is None
