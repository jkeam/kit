"""Multi-turn chat history must be sent to the LLM so short follow-ups
like "CLI" keep the prior clarifying question in context."""

import json

import pytest

from gateway.session_manager import SessionManager, make_session_id
from runtime.agent import EMPTY_REPLY_NOTICE, PersonalAssistant
from runtime.agents import AgentRegistry
from tests.test_plan import (
    _FakeClient,
    _FakeCompletions,
    _text_round,
)


@pytest.fixture
def session_manager(tmp_path, monkeypatch):
    class _NoEmbeddings:
        def __init__(self, *a, **k):
            pass

        def index_workspace(self):
            pass

        def semantic_search(self, *a, **k):
            return []

    monkeypatch.setattr("gateway.session_manager.EmbeddingsManager", _NoEmbeddings)
    monkeypatch.setattr("runtime.agent.EmbeddingsManager", _NoEmbeddings)
    workspace = tmp_path / "workspace"
    templates = tmp_path / "templates"
    templates.mkdir()
    reg = AgentRegistry(workspace_dir=str(workspace), templates_dir=str(templates))
    return SessionManager(agent_registry=reg)


def test_get_llm_history_skips_meta_and_trailing_current(session_manager):
    sid = make_session_id("web", "jon")
    session_manager.save_message(sid, "user", "write a joke telling app in ruby")
    session_manager.save_message(
        sid, "assistant", "CLI or web application?"
    )
    session_manager.save_message(sid, "user", "CLI")
    session_manager.save_message(sid, "reactions", json.dumps({"x": 1}))

    hist = session_manager.get_llm_history(sid, current_user_message="CLI")
    assert hist == [
        {"role": "user", "content": "write a joke telling app in ruby"},
        {"role": "assistant", "content": "CLI or web application?"},
    ]


def test_get_llm_history_keeps_current_when_not_yet_saved(session_manager):
    sid = make_session_id("web", "jon")
    session_manager.save_message(sid, "user", "hello")
    session_manager.save_message(sid, "assistant", "hi")

    hist = session_manager.get_llm_history(sid, current_user_message="CLI")
    assert hist[-1] == {"role": "assistant", "content": "hi"}
    assert all(m["content"] != "CLI" for m in hist)


def test_delegated_user_messages_are_labeled(session_manager):
    sid = make_session_id("web", "jon", "tester-1")
    session_manager.save_message(sid, "user", "run the suite", sender="kit")
    hist = session_manager.get_llm_history(sid)
    assert hist[0]["content"] == "[Delegated from kit] run the suite"


@pytest.mark.asyncio
async def test_chat_stream_includes_prior_turns(session_manager):
    """Reproduce the joke-app / CLI follow-up: prior turns must reach the model."""
    sid = make_session_id("web", "browser")
    session_manager.save_message(sid, "user", "write a joke telling app in ruby")
    session_manager.save_message(
        sid, "assistant",
        "Could you let me know whether you'd like the joke-teller to run "
        "from the command line (CLI) or be a simple web application?",
    )
    # Gateway persists the follow-up before calling chat_stream
    session_manager.save_message(sid, "user", "CLI")

    session = session_manager.get_session("web", "browser", "kit")
    agent = session.agent
    agent._max_context_tokens = 120_000

    captured = []

    class _CapturingCompletions(_FakeCompletions):
        async def create(self, **kwargs):
            captured.append(kwargs.get("messages", []))
            return await super().create(**kwargs)

    completions = _CapturingCompletions([_text_round("I'll build a Ruby CLI joke app.")])
    agent.client = _FakeClient(completions)

    events = []
    async for event in agent.chat_stream("CLI"):
        events.append(event)

    assert any(e["type"] == "stream_end" for e in events)
    assert captured, "expected at least one LLM call"
    roles_contents = [
        (m["role"], m.get("content", ""))
        for m in captured[0]
        if m["role"] in ("user", "assistant")
    ]
    assert ("user", "write a joke telling app in ruby") in roles_contents
    assert any(
        r == "assistant" and "CLI" in c
        for r, c in roles_contents
    )
    # Current turn once at the end — not duplicated from disk
    user_turns = [c for r, c in roles_contents if r == "user"]
    assert user_turns[-1] == "CLI"
    assert user_turns.count("CLI") == 1


@pytest.mark.asyncio
async def test_chat_stream_notifies_user_on_empty_reply(tmp_path):
    """When the model returns no visible text (even after retry), tell the user."""
    completions = _FakeCompletions([
        _text_round(""),  # first round empty
        _text_round(""),  # empty-reply retry also empty
    ])
    agent = PersonalAssistant(workspace_dir=str(tmp_path), use_embeddings=False)
    agent.client = _FakeClient(completions)
    agent._max_context_tokens = 120_000

    events = []
    async for event in agent.chat_stream("create a joke telling app in java"):
        events.append(event)

    assert completions.calls == 2
    end = next(e for e in events if e["type"] == "stream_end")
    assert end["content"] == EMPTY_REPLY_NOTICE
    assert any(
        e["type"] == "text_delta" and EMPTY_REPLY_NOTICE in e.get("content", "")
        for e in events
    )
