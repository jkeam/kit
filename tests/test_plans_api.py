"""Tests for plan cancel runtime, session mode exposure, and plan HTTP helpers."""

import json

import pytest

from gateway.session_manager import SessionManager
from runtime.agents import KIT_AGENT_ID, AgentRegistry
from tools.plan import load_plan


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
    (templates / "tester.json").write_text(json.dumps({
        "id": "tester",
        "name": "Tester",
        "description": "Runs tests.",
        "tools": ["read"],
        "skills": [],
        "soul": "# Tester",
    }))
    reg = AgentRegistry(workspace_dir=str(workspace), templates_dir=str(templates))
    reg.create_agent(template_id="tester", id="tester-1")
    return SessionManager(agent_registry=reg)


def test_session_stats_expose_mode_and_plan_id(kit_sm):
    session = kit_sm.get_session("web", "browser", KIT_AGENT_ID)
    stats = kit_sm.get_session_stats(session.session_id)
    assert stats["mode"] == "plan"
    assert stats["active_plan_id"] is None

    session.agent._execute_tool(
        "plan_present",
        {"goal": "X", "steps": [{"agent_id": "tester-1", "action": "Go"}]},
    )
    stats = kit_sm.get_session_stats(session.session_id)
    assert stats["mode"] == "plan"
    assert stats["active_plan_id"]
    session.agent._execute_tool("plan_approve", {})
    stats = kit_sm.get_session_stats(session.session_id)
    assert stats["mode"] == "orchestrate"
    assert stats["active_plan_id"]


@pytest.mark.asyncio
async def test_cancel_runs_for_user_sets_event(kit_sm):
    kit = kit_sm.get_session("web", "browser", KIT_AGENT_ID)
    teammate = kit_sm.get_session("web", "browser", "tester-1")
    assert not kit.cancel_requested.is_set()
    signalled = await kit_sm.cancel_runs_for_user("web", "browser")
    assert kit.session_id in signalled
    assert teammate.session_id in signalled
    assert kit.cancel_requested.is_set()
    assert teammate.cancel_requested.is_set()


@pytest.mark.asyncio
async def test_chat_stream_aborts_when_cancel_requested(kit_sm):
    session = kit_sm.get_session("web", "browser", KIT_AGENT_ID)
    agent = session.agent
    session.cancel_requested.set()

    events = []
    async for event in agent.chat_stream("hello"):
        events.append(event)

    assert any(e.get("type") == "stream_error" and e.get("error") == "Cancelled" for e in events)


@pytest.mark.asyncio
async def test_plan_cancel_tool_returns_to_plan_mode(kit_sm):
    session = kit_sm.get_session("web", "browser", KIT_AGENT_ID)
    agent = session.agent
    agent._execute_tool(
        "plan_present",
        {"goal": "Do work", "steps": [{"id": "s1", "agent_id": "tester-1", "action": "Go"}]},
    )
    agent._execute_tool("plan_approve", {})
    assert session.mode == "orchestrate"
    result = agent._execute_tool("plan_cancel", {"reason": "enough"})
    assert not result.startswith("Error:")
    assert session.mode == "plan"
    assert session.active_plan_id is None
    from tools.plan import latest_plan
    plan = latest_plan(str(agent.workspace_dir), statuses=["cancelled"])
    assert plan["status"] == "cancelled"
    assert plan["steps"][0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_clear_cancel_on_new_run(kit_sm):
    session = kit_sm.get_session("web", "browser", KIT_AGENT_ID)
    session.cancel_requested.set()

    class _EmptyStream:
        def __aiter__(self):
            return self._gen()

        async def _gen(self):
            if False:
                yield None

    class _FakeCompletions:
        async def create(self, **kwargs):
            return _EmptyStream()

    class _FakeClient:
        def __init__(self):
            self.chat = type("C", (), {"completions": _FakeCompletions()})()

    session.agent.client = _FakeClient()
    session.agent._max_context_tokens = 120_000

    # _run_and_track clears cancel at start
    events = []
    async for event in kit_sm._run_and_track(session, "hi"):
        events.append(event)
    assert not session.cancel_requested.is_set()
