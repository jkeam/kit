"""Deterministic plan Continue driver (bypasses Kit LLM tool calling)."""

import json

import pytest

from gateway.server import _drive_plan_steps
from gateway.session_manager import SessionManager
from runtime.agents import KIT_AGENT_ID, AgentRegistry
from tools.plan import latest_plan, load_plan
from tests.test_plan import _FakeClient, _FakeCompletions, _text_round


class _NoEmbeddings:
    def __init__(self, *args, **kwargs):
        raise RuntimeError("embeddings disabled for tests")


@pytest.fixture(autouse=True)
def _no_real_embeddings(monkeypatch):
    monkeypatch.setattr("gateway.session_manager.EmbeddingsManager", _NoEmbeddings)
    monkeypatch.setattr("runtime.agent.EmbeddingsManager", _NoEmbeddings)


@pytest.fixture
def kit_sm(tmp_path):
    workspace = tmp_path / "workspace"
    templates = tmp_path / "templates"
    templates.mkdir()
    for tid, name in (("researcher", "Ronny"), ("developer", "David")):
        (templates / f"{tid}.json").write_text(json.dumps({
            "id": tid,
            "name": name,
            "description": f"{name} does {tid}.",
            "tools": ["read", "list_files", "write"],
            "skills": [],
            "soul": f"# {name}",
        }))
    reg = AgentRegistry(workspace_dir=str(workspace), templates_dir=str(templates))
    reg.create_agent(template_id="researcher", id="researcher-1", name="Ronny")
    reg.create_agent(template_id="developer", id="dev-1", name="David")
    return SessionManager(agent_registry=reg)


@pytest.mark.asyncio
async def test_drive_plan_steps_runs_delegate_and_completes(kit_sm, monkeypatch):
    session = kit_sm.get_session("web", "browser", KIT_AGENT_ID)
    agent = session.agent
    agent._execute_tool(
        "plan_present",
        {
            "goal": "Hello app",
            "steps": [
                {"id": "1", "agent_id": "researcher-1", "action": "Research it"},
                {"id": "2", "agent_id": "dev-1", "action": "Build it", "depends_on": ["1"]},
            ],
        },
    )
    agent._execute_tool("plan_approve", {})
    plan_id = session.active_plan_id

    researcher = kit_sm.get_session("web", "browser", "researcher-1")
    researcher.agent.client = _FakeClient(_FakeCompletions([_text_round("Research notes.")]))
    researcher.agent._max_context_tokens = 120_000
    developer = kit_sm.get_session("web", "browser", "dev-1")
    developer.agent.client = _FakeClient(_FakeCompletions([_text_round("Built hello_world.py.")]))
    developer.agent._max_context_tokens = 120_000

    # Avoid WS fan-out in unit test
    async def _noop(*a, **k):
        return None

    monkeypatch.setattr("gateway.server.manager.broadcast", _noop)

    await _drive_plan_steps(kit_sm, "web", "browser", plan_id)

    plan = load_plan(plan_id, str(agent.workspace_dir))
    assert plan["status"] == "completed"
    assert plan["steps"][0]["status"] == "done"
    assert plan["steps"][1]["status"] == "done"
    assert "Research notes" in plan["steps"][0]["result"]
    assert session.mode == "plan"
