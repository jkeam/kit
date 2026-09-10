"""Tests for the agent_delegate tool: the guard conditions in
PersonalAssistant._delegate (runtime/agent.py), and a full round trip
through SessionManager.delegate (gateway/session_manager.py) verifying a
delegated task lands in the target agent's own persistent thread, tagged
with who sent it.
"""

import json

import pytest

from gateway.session_manager import SessionManager
from runtime.agent import MAX_DELEGATION_DEPTH, PersonalAssistant
from runtime.agents import AgentRegistry


# --- A minimal fake OpenAI-shaped streaming client, just enough of the
# surface PersonalAssistant.chat_stream() touches to drive a scripted
# tool-call round trip without any real LLM. ---

class _Delta:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _ToolCallFunction:
    def __init__(self, name=None, arguments=None):
        self.name = name
        self.arguments = arguments


class _ToolCall:
    def __init__(self, index, id=None, function=None):
        self.index = index
        self.id = id
        self.function = function


class _Choice:
    def __init__(self, delta, finish_reason=None):
        self.delta = delta
        self.finish_reason = finish_reason


class _Chunk:
    def __init__(self, choices):
        self.choices = choices


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for chunk in self._chunks:
            yield chunk


def _tool_call_round(tool_name, arguments: dict, call_id="call_1"):
    return [
        _Chunk([_Choice(_Delta(tool_calls=[
            _ToolCall(0, id=call_id, function=_ToolCallFunction(name=tool_name, arguments=json.dumps(arguments)))
        ]))]),
        _Chunk([_Choice(_Delta(), finish_reason="tool_calls")]),
    ]


def _text_round(text):
    return [
        _Chunk([_Choice(_Delta(content=text))]),
        _Chunk([_Choice(_Delta(), finish_reason="stop")]),
    ]


class _FakeCompletions:
    def __init__(self, rounds):
        self._rounds = list(rounds)

    async def create(self, model, messages, tools, stream=True):
        return _FakeStream(self._rounds.pop(0))


class _FakeChat:
    def __init__(self, rounds):
        self.completions = _FakeCompletions(rounds)


class FakeClient:
    """Drop-in replacement for agent.client, scripted with one list of
    chunks per expected round of chat.completions.create()."""

    def __init__(self, rounds):
        self.chat = _FakeChat(rounds)


class _NoEmbeddings:
    def __init__(self, *args, **kwargs):
        raise RuntimeError("embeddings disabled for tests")


@pytest.fixture(autouse=True)
def _no_real_embeddings(monkeypatch):
    monkeypatch.setattr("gateway.session_manager.EmbeddingsManager", _NoEmbeddings)
    monkeypatch.setattr("runtime.agent.EmbeddingsManager", _NoEmbeddings)


# --- Unit tests for the _delegate guard conditions ---

@pytest.fixture
def bare_agent(tmp_path):
    return PersonalAssistant(workspace_dir=str(tmp_path), use_embeddings=False)


@pytest.mark.asyncio
async def test_delegate_blocked_when_tool_not_allowlisted(tmp_path):
    agent = PersonalAssistant(
        workspace_dir=str(tmp_path), use_embeddings=False, allowed_tools={"read"}
    )
    result = await agent._delegate({"agent_id": "tester-1", "task": "run tests"})
    assert "not available to this agent" in result


@pytest.mark.asyncio
async def test_delegate_blocked_without_session_manager(bare_agent):
    result = await bare_agent._delegate({"agent_id": "tester-1", "task": "run tests"})
    assert "not available in this context" in result


@pytest.mark.asyncio
async def test_delegate_requires_agent_id(tmp_path):
    agent = PersonalAssistant(
        workspace_dir=str(tmp_path),
        use_embeddings=False,
        session_manager=object(),
        platform="web",
        user_id="browser",
    )
    result = await agent._delegate({"task": "run tests"})
    assert "requires 'agent_id'" in result


@pytest.mark.asyncio
async def test_delegate_depth_guard_trips(tmp_path):
    agent = PersonalAssistant(
        workspace_dir=str(tmp_path),
        use_embeddings=False,
        session_manager=object(),
        platform="web",
        user_id="browser",
    )
    agent._current_delegation_depth = MAX_DELEGATION_DEPTH
    result = await agent._delegate({"agent_id": "tester-1", "task": "run tests"})
    assert "delegation depth limit" in result


# --- Full round trip through SessionManager.delegate ---

@pytest.fixture
def registry(tmp_path):
    workspace = tmp_path / "workspace"
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "tester.json").write_text(json.dumps({
        "id": "tester",
        "name": "Tester",
        "description": "",
        "tools": ["read", "list_files"],
        "skills": [],
        "soul": "# Tester",
    }))
    reg = AgentRegistry(workspace_dir=str(workspace), templates_dir=str(templates))
    reg.create_agent(template_id="tester", id="tester-1")
    return reg


@pytest.fixture
def session_manager(registry):
    return SessionManager(agent_registry=registry)


@pytest.mark.asyncio
async def test_kit_delegates_to_tester_and_gets_reply(session_manager):
    # Pre-create tester-1's session so we can script its client before Kit's
    # delegation call reaches it (SessionManager caches sessions by id, so
    # the same agent instance/client gets reused).
    tester_session = session_manager.get_session("web", "browser", "tester-1")
    tester_session.agent.client = FakeClient([_text_round("All 12 tests passed.")])

    kit_session = session_manager.get_session("web", "browser", "kit")
    kit_session.agent.client = FakeClient([
        _tool_call_round("agent_delegate", {"agent_id": "tester-1", "task": "run the test suite"}),
        _text_round("The tester reports all 12 tests passed."),
    ])

    reply = await kit_session.agent.chat("have the tester run the suite")

    assert reply == "The tester reports all 12 tests passed."

    tester_messages = session_manager.get_messages(tester_session.session_id)
    delegated = [m for m in tester_messages if m["role"] == "user"]
    assert delegated[-1]["content"] == "run the test suite"
    assert delegated[-1]["sender"] == "kit"

    tester_replies = [m for m in tester_messages if m["role"] == "assistant"]
    assert tester_replies[-1]["content"] == "All 12 tests passed."


@pytest.mark.asyncio
async def test_delegation_to_agent_without_the_tool_still_only_that_agent_acts(session_manager):
    """tester-1's template doesn't include agent_delegate, so even if asked
    to delegate further, it can't - only the originally-addressed agent
    (tester-1) can ever act on the task."""
    tester_session = session_manager.get_session("web", "browser", "tester-1")
    assert tester_session.agent.allowed_tools is not None
    assert "agent_delegate" not in tester_session.agent.allowed_tools


def test_kit_sees_teammates_in_its_system_prompt(session_manager):
    """Regression test: Kit must actually be told who's on the team,
    otherwise it has the agent_delegate tool but no valid agent_id to pass
    it. The roster is resolved fresh from the registry (not cached at
    construction), so an agent created after Kit's session already exists
    still shows up."""
    kit_session = session_manager.get_session("web", "browser", "kit")
    session_manager.get_session("web", "browser", "tester-1")  # created after Kit's session

    prompt = kit_session.agent._build_system_prompt()

    assert "YOUR TEAM" in prompt
    assert "tester-1" in prompt


def test_agent_without_delegate_tool_sees_no_roster(session_manager):
    """tester-1 can't delegate, so cluttering its prompt with a team roster
    it can't act on would be pointless - it shouldn't see one."""
    tester_session = session_manager.get_session("web", "browser", "tester-1")
    prompt = tester_session.agent._build_system_prompt()
    assert "YOUR TEAM" not in prompt
