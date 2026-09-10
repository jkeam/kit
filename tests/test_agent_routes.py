"""Tests for the /agents, /agent-templates, /tools, /skills REST endpoints
in gateway/server.py. Calls the route handler coroutines directly (same
approach as tests/test_gateway_auth.py) with `gateway.server.session_manager`
monkeypatched to a real SessionManager over a tmp workspace, so these don't
pay for FastAPI's app startup (which loads the real embedding model).
"""

import json

import pytest
from fastapi import HTTPException

import gateway.server as server_module
from gateway.session_manager import SessionManager
from runtime.agents import AgentRegistry


class _NoEmbeddings:
    def __init__(self, *args, **kwargs):
        raise RuntimeError("embeddings disabled for tests")


@pytest.fixture(autouse=True)
def _no_real_embeddings(monkeypatch):
    monkeypatch.setattr("gateway.session_manager.EmbeddingsManager", _NoEmbeddings)
    monkeypatch.setattr("runtime.agent.EmbeddingsManager", _NoEmbeddings)


@pytest.fixture
def registry(tmp_path):
    workspace = tmp_path / "workspace"
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "tester.json").write_text(json.dumps({
        "id": "tester", "name": "Tester", "description": "Runs tests.",
        "tools": ["read", "list_files"], "skills": [], "soul": "# Tester",
    }))
    return AgentRegistry(workspace_dir=str(workspace), templates_dir=str(templates))


@pytest.fixture(autouse=True)
def _wire_session_manager(monkeypatch, registry):
    sm = SessionManager(agent_registry=registry)
    monkeypatch.setattr(server_module, "session_manager", sm)
    return sm


@pytest.mark.asyncio
async def test_list_agents_includes_kit():
    agents = await server_module.list_agents()
    assert any(a.id == "kit" for a in agents)


@pytest.mark.asyncio
async def test_create_and_get_agent():
    created = await server_module.create_agent(
        server_module.CreateAgentRequest(template_id="tester", id="tester-1")
    )
    assert created.id == "tester-1"
    assert created.tools == ["read", "list_files"]

    fetched = await server_module.get_agent("tester-1")
    assert fetched.soul == "# Tester"


@pytest.mark.asyncio
async def test_create_agent_with_bad_template_returns_400():
    with pytest.raises(HTTPException) as exc_info:
        await server_module.create_agent(
            server_module.CreateAgentRequest(template_id="does-not-exist", id="x")
        )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_get_unknown_agent_returns_404():
    with pytest.raises(HTTPException) as exc_info:
        await server_module.get_agent("does-not-exist")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_update_agent():
    await server_module.create_agent(
        server_module.CreateAgentRequest(template_id="tester", id="tester-1")
    )
    updated = await server_module.update_agent(
        "tester-1", server_module.UpdateAgentRequest(tools=["read"])
    )
    assert updated.tools == ["read"]


@pytest.mark.asyncio
async def test_update_agent_soul():
    await server_module.create_agent(
        server_module.CreateAgentRequest(template_id="tester", id="tester-1")
    )
    updated = await server_module.update_agent(
        "tester-1", server_module.UpdateAgentRequest(soul="# New persona")
    )
    assert updated.soul == "# New persona"


@pytest.mark.asyncio
async def test_update_agent_accepts_wildcard_tools_and_skills():
    await server_module.create_agent(
        server_module.CreateAgentRequest(template_id="tester", id="tester-1")
    )
    updated = await server_module.update_agent(
        "tester-1", server_module.UpdateAgentRequest(tools="*", skills="*")
    )
    assert updated.tools == "*"
    assert updated.skills == "*"


@pytest.mark.asyncio
async def test_create_agent_accepts_wildcard_tools():
    created = await server_module.create_agent(
        server_module.CreateAgentRequest(template_id="tester", id="tester-2", tools="*")
    )
    assert created.tools == "*"


@pytest.mark.asyncio
async def test_delete_agent():
    await server_module.create_agent(
        server_module.CreateAgentRequest(template_id="tester", id="tester-1")
    )
    result = await server_module.delete_agent("tester-1")
    assert "deleted" in result["message"]
    with pytest.raises(HTTPException):
        await server_module.get_agent("tester-1")


@pytest.mark.asyncio
async def test_delete_kit_returns_400():
    with pytest.raises(HTTPException) as exc_info:
        await server_module.delete_agent("kit")
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_list_agent_templates_includes_built_in():
    templates = await server_module.list_agent_templates()
    assert any(t["id"] == "tester" for t in templates)


@pytest.mark.asyncio
async def test_save_agent_template():
    saved = await server_module.save_agent_template({
        "id": "custom-role", "name": "Custom", "description": "",
        "tools": [], "skills": [], "soul": "",
    })
    assert saved["id"] == "custom-role"
    templates = await server_module.list_agent_templates()
    assert any(t["id"] == "custom-role" for t in templates)


@pytest.mark.asyncio
async def test_save_agent_template_missing_id_returns_400():
    with pytest.raises(HTTPException) as exc_info:
        await server_module.save_agent_template({
            "name": "No Id", "tools": [], "skills": [], "soul": "",
        })
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_save_agent_template_missing_name_returns_400():
    with pytest.raises(HTTPException) as exc_info:
        await server_module.save_agent_template({
            "id": "no-name", "tools": [], "skills": [], "soul": "",
        })
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_save_agent_template_overrides_existing():
    await server_module.save_agent_template({
        "id": "custom-role", "name": "V1", "description": "first",
        "tools": [], "skills": [], "soul": "",
    })
    await server_module.save_agent_template({
        "id": "custom-role", "name": "V2", "description": "second",
        "tools": ["read"], "skills": [], "soul": "# Updated",
    })
    templates = await server_module.list_agent_templates()
    custom = next(t for t in templates if t["id"] == "custom-role")
    assert custom["name"] == "V2"
    assert custom["tools"] == ["read"]


@pytest.mark.asyncio
async def test_list_tools_returns_known_tool_names():
    tools = await server_module.list_tools()
    names = {t["name"] for t in tools}
    assert "read" in names
    assert "agent_delegate" in names


@pytest.mark.asyncio
async def test_list_skills_endpoint_empty_by_default():
    skills = await server_module.list_skills_endpoint()
    assert skills == []


@pytest.mark.asyncio
async def test_agents_status_and_activity_start_empty():
    assert await server_module.get_agents_status() == {}
    assert await server_module.get_agents_activity() == []
