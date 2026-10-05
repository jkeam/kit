"""Tests for fuzzy agent_delegate args and orchestration stall recovery."""

import json

import pytest

from gateway.session_manager import SessionManager
from runtime.agents import KIT_AGENT_ID, AgentRegistry
from tests.test_plan import (
    _FakeClient,
    _FakeCompletions,
    _text_round,
    _Chunk,
    _Choice,
    _Delta,
)


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
    (templates / "researcher.json").write_text(json.dumps({
        "id": "researcher",
        "name": "Researcher",
        "description": "Researches.",
        "tools": ["read", "list_files"],
        "skills": [],
        "soul": "# Researcher",
    }))
    reg = AgentRegistry(workspace_dir=str(workspace), templates_dir=str(templates))
    reg.create_agent(template_id="researcher", id="researcher-1", name="Ronny")
    return SessionManager(agent_registry=reg)


def test_resolve_teammate_id_fuzzy(kit_sm):
    session = kit_sm.get_session("web", "browser", KIT_AGENT_ID)
    agent = session.agent
    assert agent._resolve_teammate_id("researcher-1") == "researcher-1"
    assert agent._resolve_teammate_id("researcher") == "researcher-1"
    assert agent._resolve_teammate_id("Ronny") == "researcher-1"
    assert agent._resolve_teammate_id("nope") is None


def test_normalize_delegate_args_maps_description_and_step(kit_sm):
    session = kit_sm.get_session("web", "browser", KIT_AGENT_ID)
    agent = session.agent
    agent._execute_tool(
        "plan_present",
        {
            "goal": "Hello",
            "steps": [{
                "id": "1",
                "agent_id": "researcher-1",
                "action": "Research hello world best practices",
                "success_criteria": "A short report",
            }],
        },
    )
    agent._execute_tool("plan_approve", {})
    args = agent._normalize_delegate_args({
        "agent_id": "researcher",
        "step_id": "1",
        "description": "",
        "parameters": {},
    })
    assert args["agent_id"] == "researcher-1"
    assert "Research hello world" in args["task"]
    assert "Success criteria" in args["task"]


def _harmony_text_round(text: str):
    return [
        _Chunk([_Choice(_Delta(content=text))]),
        _Chunk([_Choice(_Delta(), finish_reason="stop")]),
    ]


@pytest.mark.asyncio
async def test_orch_stall_nudge_then_delegate(kit_sm):
    """Empty first round while plan has ready steps → nudge → second round tools."""
    session = kit_sm.get_session("web", "browser", KIT_AGENT_ID)
    agent = session.agent
    agent._execute_tool(
        "plan_present",
        {"goal": "Ship", "steps": [{"id": "1", "agent_id": "researcher-1", "action": "Research"}]},
    )
    agent._execute_tool("plan_approve", {})
    assert session.mode == "orchestrate"

    researcher = kit_sm.get_session("web", "browser", "researcher-1")
    researcher.agent.client = _FakeClient(_FakeCompletions([_text_round("Findings ready.")]))
    researcher.agent._max_context_tokens = 120_000

    from tests.test_plan import _tool_call_round
    completions = _FakeCompletions([
        _text_round("I'll get started shortly."),  # stall — no tools
        _tool_call_round("agent_delegate", {
            "agent_id": "researcher",
            "description": "Research the topic",
        }),
        _text_round("Researcher finished."),
    ])
    agent.client = _FakeClient(completions)
    agent._max_context_tokens = 120_000

    events = []
    async for event in agent.chat_stream("go"):
        events.append(event)

    assert any(
        e["type"] == "tool_call_start" and e["tool_name"] == "agent_delegate"
        for e in events
    )
    # Fuzzy id repaired → researcher-1 actually ran
    msgs = kit_sm.get_messages(researcher.session_id)
    assert any(m["role"] == "user" and "Research" in m["content"] for m in msgs)
