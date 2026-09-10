"""Tests for agent-aware session identity/resolution in
gateway/session_manager.py: each (platform, user, agent) triple gets its
own persistent thread, "kit" keeps the original session id for backward
compatibility, and a session's agent gets the right tool/skill/persona
scoping resolved from the AgentRegistry.
"""

import pytest

from gateway.session_manager import SessionManager, make_session_id
from runtime.agents import AgentRegistry


class _NoEmbeddings:
    """Stand-in for EmbeddingsManager that always fails to init, so tests
    don't pay to load a real SentenceTransformer model."""

    def __init__(self, *args, **kwargs):
        raise RuntimeError("embeddings disabled for tests")


@pytest.fixture(autouse=True)
def _no_real_embeddings(monkeypatch):
    # SessionManager's own shared instance, and PersonalAssistant's fallback
    # (built per-agent whenever the shared one failed to init) both need
    # stubbing out, or a real SentenceTransformer model gets loaded.
    monkeypatch.setattr("gateway.session_manager.EmbeddingsManager", _NoEmbeddings)
    monkeypatch.setattr("runtime.agent.EmbeddingsManager", _NoEmbeddings)


@pytest.fixture
def registry(tmp_path):
    workspace = tmp_path / "workspace"
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "tester.json").write_text(
        '{"id": "tester", "name": "Tester", "description": "", '
        '"tools": ["read", "list_files"], "skills": [], "soul": "# Tester"}'
    )
    reg = AgentRegistry(workspace_dir=str(workspace), templates_dir=str(templates))
    reg.create_agent(template_id="tester", id="tester-1")
    return reg


@pytest.fixture
def session_manager(registry):
    return SessionManager(agent_registry=registry)


def test_make_session_id_kit_unchanged():
    assert make_session_id("web", "browser", "kit") == "web:browser"


def test_make_session_id_other_agent_gets_suffix():
    assert make_session_id("web", "browser", "tester-1") == "web:browser:tester-1"


def test_get_session_defaults_to_kit(session_manager):
    session = session_manager.get_session("web", "browser")
    assert session.agent_id == "kit"
    assert session.session_id == "web:browser"
    assert session.agent.allowed_tools is None


def test_get_session_for_specific_agent_scopes_tools(session_manager):
    session = session_manager.get_session("web", "browser", agent_id="tester-1")
    assert session.session_id == "web:browser:tester-1"
    assert session.agent.allowed_tools == {"read", "list_files"}
    assert session.agent.soul == "# Tester"


def test_kit_and_agent_threads_are_independent(session_manager):
    kit_session = session_manager.get_session("web", "browser", agent_id="kit")
    tester_session = session_manager.get_session("web", "browser", agent_id="tester-1")
    assert kit_session.session_id != tester_session.session_id
    assert kit_session.agent is not tester_session.agent


def test_get_session_rejects_unknown_agent(session_manager):
    with pytest.raises(ValueError):
        session_manager.get_session("web", "browser", agent_id="does-not-exist")
