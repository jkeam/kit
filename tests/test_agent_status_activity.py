"""Tests for live per-agent status tracking and the cross-agent activity
feed in gateway/session_manager.py (SessionManager._run_and_track /
get_agent_status / get_activity) - the "which agents are busy, and what are
they doing" and "inspect all agent-to-agent communication" surfaces.
"""

import json

import pytest

from gateway.session_manager import SessionManager
from runtime.agents import AgentRegistry
from tests.test_delegation import FakeClient, _text_round, _tool_call_round


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
        "id": "tester", "name": "Tester", "description": "",
        "tools": ["read", "list_files"], "skills": [], "soul": "# Tester",
    }))
    reg = AgentRegistry(workspace_dir=str(workspace), templates_dir=str(templates))
    reg.create_agent(template_id="tester", id="tester-1")
    return reg


@pytest.fixture
def session_manager(registry):
    return SessionManager(agent_registry=registry)


@pytest.mark.asyncio
async def test_status_is_busy_during_tool_call_and_idle_after(session_manager):
    session = session_manager.get_session("web", "browser", "tester-1")
    session.agent.client = FakeClient([
        _tool_call_round("read", {"path": "hello.txt"}),
        _text_round("done"),
    ])

    seen_statuses = []
    async for event in session_manager._run_and_track(session, "read hello.txt"):
        seen_statuses.append(session_manager.get_agent_status()["tester-1"]["status"])

    # Busy at least once (during the tool call), idle by the end.
    assert "busy" in seen_statuses
    assert session_manager.get_agent_status()["tester-1"]["status"] == "idle"


@pytest.mark.asyncio
async def test_current_task_reflects_the_tool_being_called(session_manager):
    session = session_manager.get_session("web", "browser", "tester-1")
    session.agent.client = FakeClient([
        _tool_call_round("read", {"path": "hello.txt"}),
        _text_round("done"),
    ])

    async for _ in session_manager._run_and_track(session, "read hello.txt"):
        pass

    activity = session_manager.get_activity(agent_id="tester-1")
    tool_starts = [e for e in activity if e["event_type"] == "tool_call_start"]
    assert len(tool_starts) == 1
    assert tool_starts[0]["detail"]["tool_name"] == "read"


@pytest.mark.asyncio
async def test_activity_feed_is_filterable_by_agent(session_manager):
    kit_session = session_manager.get_session("web", "browser", "kit")
    kit_session.agent.client = FakeClient([_text_round("hi")])
    tester_session = session_manager.get_session("web", "browser", "tester-1")
    tester_session.agent.client = FakeClient([_text_round("hi")])

    async for _ in session_manager._run_and_track(kit_session, "hello"):
        pass
    async for _ in session_manager._run_and_track(tester_session, "hello"):
        pass

    all_activity = session_manager.get_activity()
    kit_activity = session_manager.get_activity(agent_id="kit")
    assert any(e["agent_id"] == "kit" for e in all_activity)
    assert any(e["agent_id"] == "tester-1" for e in all_activity)
    assert all(e["agent_id"] == "kit" for e in kit_activity)


@pytest.mark.asyncio
async def test_on_event_callback_receives_live_events(session_manager):
    received = []

    async def on_event(entry):
        received.append(entry)

    session_manager.on_event = on_event
    session = session_manager.get_session("web", "browser", "tester-1")
    session.agent.client = FakeClient([_text_round("hi")])

    async for _ in session_manager._run_and_track(session, "hello"):
        pass

    event_types = {e["event_type"] for e in received}
    assert "agent_status" in event_types
