"""Tests for plan tools: present/approve/orchestrate/complete, Kit mode
switching, and ReAct loop stop behavior.
"""

import json

import pytest

from gateway.session_manager import SessionManager, make_session_id
from runtime.agent import PersonalAssistant
from runtime.agents import (
    KIT_AGENT_ID,
    KIT_MANAGER_TOOLS,
    KIT_ORCHESTRATE_TOOLS,
    KIT_PLAN_TOOLS,
    AgentRegistry,
)
from tools.plan import (
    load_plan,
    plan_approve,
    plan_cancel,
    plan_complete,
    plan_present,
    plan_reject,
    plan_revise,
    plan_step_update,
    ready_steps,
)


def test_plan_present_persists_pending_json(tmp_path):
    markdown = plan_present(
        goal="Add dark mode",
        steps=[
            {
                "agent_id": "researcher",
                "action": "Survey how theming works",
                "success_criteria": "A short report of relevant files",
            },
            {
                "id": "s2",
                "agent_id": "developer",
                "action": "Implement the theme toggle",
                "depends_on": ["s1"],
            },
        ],
        risks=["May touch shared CSS"],
        open_questions=["Which default theme?"],
        workspace_dir=str(tmp_path),
    )
    plans = list((tmp_path / "plans").glob("plan_*.json"))
    assert len(plans) == 1
    data = json.loads(plans[0].read_text())
    assert data["status"] == "pending"
    assert data["goal"] == "Add dark mode"
    assert len(data["steps"]) == 2
    assert data["steps"][0]["id"] == "s1"
    assert data["steps"][0]["status"] == "pending"
    assert data["steps"][1]["depends_on"] == ["s1"]
    assert "awaiting your approval" in markdown
    assert "researcher" in markdown
    assert data["id"] in markdown


def test_plan_present_rejects_empty_goal(tmp_path):
    result = plan_present(goal="  ", steps=[{"agent_id": "dev", "action": "x"}], workspace_dir=str(tmp_path))
    assert result.startswith("Error:")
    assert not (tmp_path / "plans").exists() or not list((tmp_path / "plans").glob("*.json"))


def test_plan_present_rejects_missing_step_fields(tmp_path):
    result = plan_present(
        goal="Do a thing",
        steps=[{"agent_id": "dev"}],
        workspace_dir=str(tmp_path),
    )
    assert "action" in result


def test_plan_present_accepts_json_string_steps(tmp_path):
    result = plan_present(
        goal="Research APIs",
        steps=json.dumps([{"agent_id": "researcher", "action": "Find docs"}]),
        workspace_dir=str(tmp_path),
    )
    assert not result.startswith("Error:")
    assert "researcher" in result


def test_plan_approve_and_ready_steps(tmp_path):
    plan_present(
        goal="Ship feature",
        steps=[
            {"id": "s1", "agent_id": "researcher", "action": "Research"},
            {"id": "s2", "agent_id": "developer", "action": "Build", "depends_on": ["s1"]},
            {"id": "s3", "agent_id": "tester", "action": "Test", "depends_on": ["s2"]},
        ],
        workspace_dir=str(tmp_path),
    )
    md = plan_approve(workspace_dir=str(tmp_path))
    assert "orchestrate" in md.lower()
    from tools.plan import latest_plan
    plan = latest_plan(str(tmp_path), statuses=["approved"])
    assert plan is not None
    assert plan["status"] == "approved"
    assert [s["id"] for s in ready_steps(plan)] == ["s1"]

    plan_step_update("s1", "done", result="Found docs", workspace_dir=str(tmp_path))
    plan = latest_plan(str(tmp_path), statuses=["running"])
    assert [s["id"] for s in ready_steps(plan)] == ["s2"]


def test_garbage_depends_on_does_not_block_ready_steps(tmp_path):
    """Markdown table bleed used to make every step wait on a fake dep."""
    from tools.plan import latest_plan, ready_steps

    plan_present(
        goal="JS joke app",
        steps=[
            {
                "id": "1",
                "agent_id": "researcher-1",
                "action": "Research",
                "depends_on": [
                    "| 2 | Design the application architecture | `dev-1` | doc"
                ],
            },
            {
                "id": "3",
                "agent_id": "dev-1",
                "action": "Implement",
                "depends_on": ["4"],
            },
        ],
        workspace_dir=str(tmp_path),
    )
    plan_approve(workspace_dir=str(tmp_path))
    plan = latest_plan(str(tmp_path), statuses=["approved"])
    assert plan["steps"][0]["depends_on"] == []
    assert plan["steps"][1]["depends_on"] == []
    ready_ids = [s["id"] for s in ready_steps(plan)]
    assert "1" in ready_ids
    assert "3" in ready_ids


def test_plan_revise_updates_pending(tmp_path):
    plan_present(
        goal="Old goal",
        steps=[{"agent_id": "developer", "action": "Do A"}],
        workspace_dir=str(tmp_path),
    )
    md = plan_revise(
        goal="New goal",
        steps=[{"agent_id": "tester", "action": "Do B"}],
        workspace_dir=str(tmp_path),
    )
    assert "New goal" in md
    assert "tester" in md
    from tools.plan import latest_plan
    plan = latest_plan(str(tmp_path), statuses=["pending"])
    assert plan["goal"] == "New goal"
    assert plan["steps"][0]["agent_id"] == "tester"


def test_plan_complete_requires_all_done(tmp_path):
    plan_present(
        goal="X",
        steps=[
            {"id": "s1", "agent_id": "a", "action": "one"},
            {"id": "s2", "agent_id": "b", "action": "two"},
        ],
        workspace_dir=str(tmp_path),
    )
    plan_approve(workspace_dir=str(tmp_path))
    plan_step_update("s1", "done", workspace_dir=str(tmp_path))
    err = plan_complete(workspace_dir=str(tmp_path))
    assert err.startswith("Error:")
    plan_step_update("s2", "done", workspace_dir=str(tmp_path))
    ok = plan_complete(summary="Shipped", workspace_dir=str(tmp_path))
    assert "completed" in ok.lower()
    assert "mode returned to **plan**" in ok.lower()


def test_plan_reject(tmp_path):
    plan_present(
        goal="Nope",
        steps=[{"agent_id": "dev", "action": "x"}],
        workspace_dir=str(tmp_path),
    )
    md = plan_reject(reason="user cancelled", workspace_dir=str(tmp_path))
    assert "rejected" in md.lower()
    from tools.plan import latest_plan
    assert latest_plan(str(tmp_path), statuses=["rejected"])["reject_reason"] == "user cancelled"


def test_plan_cancel_marks_unfinished_steps(tmp_path):
    plan_present(
        goal="Long job",
        steps=[
            {"id": "s1", "agent_id": "dev", "action": "Build"},
            {"id": "s2", "agent_id": "tester", "action": "Test", "depends_on": ["s1"]},
        ],
        workspace_dir=str(tmp_path),
    )
    plan_approve(workspace_dir=str(tmp_path))
    plan_step_update("s1", "running", workspace_dir=str(tmp_path))
    md = plan_cancel(reason="stop please", workspace_dir=str(tmp_path))
    assert "cancelled" in md.lower()
    from tools.plan import latest_plan
    plan = latest_plan(str(tmp_path), statuses=["cancelled"])
    assert plan["status"] == "cancelled"
    assert plan["cancel_reason"] == "stop please"
    assert plan["steps"][0]["status"] == "cancelled"
    assert plan["steps"][1]["status"] == "cancelled"


def test_plan_present_supersedes_active(tmp_path):
    first = plan_present(
        goal="First",
        steps=[{"agent_id": "dev", "action": "a"}],
        workspace_dir=str(tmp_path),
    )
    first_id = [p for p in first.split("`") if p.startswith("plan_")][0]
    plan_approve(plan_id=first_id, workspace_dir=str(tmp_path))
    plan_present(
        goal="Second",
        steps=[{"agent_id": "tester", "action": "b"}],
        workspace_dir=str(tmp_path),
    )
    assert load_plan(first_id, str(tmp_path))["status"] == "superseded"


def test_kit_roster_shown_without_delegate(tmp_path):
    class _SM:
        def __init__(self, registry):
            self.agent_registry = registry

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
    registry = AgentRegistry(workspace_dir=str(tmp_path / "ws"), templates_dir=str(templates))
    registry.create_agent(template_id="tester", id="tester-1")
    agent = PersonalAssistant(
        workspace_dir=str(tmp_path / "ws"),
        use_embeddings=False,
        allowed_tools=set(KIT_MANAGER_TOOLS),
        agent_id=KIT_AGENT_ID,
        session_manager=_SM(registry),
    )
    section = agent._team_roster_section()
    assert "tester-1" in section
    assert "plan_present" in section
    assert "cannot delegate" in section.lower()


def test_kit_execute_rejects_write(tmp_path):
    registry = AgentRegistry(workspace_dir=str(tmp_path), templates_dir=str(tmp_path / "no-templates"))
    kit = registry.resolve(KIT_AGENT_ID)
    agent = PersonalAssistant(
        workspace_dir=str(tmp_path),
        use_embeddings=False,
        allowed_tools=kit.allowed_tools,
        agent_id=KIT_AGENT_ID,
    )
    result = agent._execute_tool("write", {"path": "x.txt", "content": "nope"})
    assert "not available to this agent" in result
    names = {t["function"]["name"] for t in agent._filtered_tools}
    assert names == set(KIT_PLAN_TOOLS)


def test_kit_plan_present_via_execute_tool(tmp_path):
    agent = PersonalAssistant(
        workspace_dir=str(tmp_path),
        use_embeddings=False,
        allowed_tools=set(KIT_MANAGER_TOOLS),
        agent_id=KIT_AGENT_ID,
    )
    result = agent._execute_tool(
        "plan_present",
        {"goal": "Ship it", "steps": [{"agent_id": "tester", "action": "Run tests"}]},
    )
    assert result.startswith("# Plan:")
    assert list((tmp_path / "plans").glob("plan_*.json"))


class _NoEmbeddings:
    def __init__(self, *args, **kwargs):
        raise RuntimeError("embeddings disabled for tests")


@pytest.fixture(autouse=True)
def _no_real_embeddings(monkeypatch):
    monkeypatch.setattr("gateway.session_manager.EmbeddingsManager", _NoEmbeddings)
    monkeypatch.setattr("runtime.agent.EmbeddingsManager", _NoEmbeddings)


@pytest.fixture
def kit_session_manager(tmp_path):
    workspace = tmp_path / "workspace"
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "tester.json").write_text(json.dumps({
        "id": "tester",
        "name": "Tester",
        "description": "Runs tests.",
        "tools": ["read", "list_files"],
        "skills": [],
        "soul": "# Tester",
    }))
    reg = AgentRegistry(workspace_dir=str(workspace), templates_dir=str(templates))
    reg.create_agent(template_id="tester", id="tester-1")
    return SessionManager(agent_registry=reg)


def test_approve_switches_kit_to_orchestrate_tools(kit_session_manager):
    session = kit_session_manager.get_session("web", "browser", "kit")
    agent = session.agent
    assert session.mode == "plan"
    assert "agent_delegate" not in agent.allowed_tools

    agent._execute_tool(
        "plan_present",
        {"goal": "Test suite", "steps": [{"id": "s1", "agent_id": "tester-1", "action": "Run tests"}]},
    )
    result = agent._execute_tool("plan_approve", {})
    assert not result.startswith("Error:")
    assert session.mode == "orchestrate"
    assert session.active_plan_id
    assert set(agent.allowed_tools) == set(KIT_ORCHESTRATE_TOOLS)
    assert "agent_delegate" in agent.allowed_tools
    assert "plan_present" in agent.allowed_tools  # can supersede
    assert "write" not in agent.allowed_tools


def test_complete_returns_to_plan_mode(kit_session_manager):
    session = kit_session_manager.get_session("web", "browser", "kit")
    agent = session.agent
    agent._execute_tool(
        "plan_present",
        {"goal": "One step", "steps": [{"id": "s1", "agent_id": "tester-1", "action": "Go"}]},
    )
    agent._execute_tool("plan_approve", {})
    agent._execute_tool(
        "plan_step_update",
        {"step_id": "s1", "status": "done", "result": "ok"},
    )
    result = agent._execute_tool("plan_complete", {"summary": "Done"})
    assert "completed" in result.lower()
    assert session.mode == "plan"
    assert set(agent.allowed_tools) == set(KIT_PLAN_TOOLS)


def test_clarification_blocks_plan(kit_session_manager):
    session = kit_session_manager.get_session("web", "browser", "kit")
    agent = session.agent
    agent._execute_tool(
        "plan_present",
        {"goal": "Clarify", "steps": [{"id": "s1", "agent_id": "tester-1", "action": "Maybe"}]},
    )
    agent._execute_tool("plan_approve", {})
    result = agent._execute_tool(
        "plan_step_update",
        {
            "step_id": "s1",
            "status": "needs_clarification",
            "clarification": "Which suite?",
        },
    )
    assert "blocked" in result.lower()
    assert "Which suite?" in result
    plan = load_plan(session.active_plan_id, str(agent.workspace_dir))
    assert plan["status"] == "blocked"
    assert plan["steps"][0]["status"] == "needs_clarification"


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


class _FakeCompletions:
    def __init__(self, rounds):
        self._rounds = list(rounds)
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        return _FakeStream(self._rounds.pop(0))


class _FakeChat:
    def __init__(self, completions):
        self.completions = completions


class _FakeClient:
    def __init__(self, completions):
        self.chat = _FakeChat(completions)


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


@pytest.mark.asyncio
async def test_chat_stream_stops_after_plan_present(tmp_path):
    """A successful plan_present must not start another LLM round."""
    completions = _FakeCompletions([
        _tool_call_round("plan_present", {
            "goal": "Add logging",
            "steps": [{"agent_id": "developer", "action": "Add a logger"}],
        }),
        _text_round("this round must not run"),
    ])
    agent = PersonalAssistant(
        workspace_dir=str(tmp_path),
        use_embeddings=False,
        allowed_tools=set(KIT_MANAGER_TOOLS),
        agent_id=KIT_AGENT_ID,
    )
    agent.client = _FakeClient(completions)
    agent._max_context_tokens = 120_000

    events = []
    async for event in agent.chat_stream("add logging"):
        events.append(event)

    assert completions.calls == 1
    texts = "".join(e.get("content", "") for e in events if e["type"] == "text_delta")
    assert "Add logging" in texts
    assert "this round must not run" not in texts
    assert any(e["type"] == "tool_call_result" and e["tool_name"] == "plan_present" for e in events)


@pytest.mark.asyncio
async def test_chat_stream_continues_after_approve_with_delegate_tools(kit_session_manager):
    """After plan_approve, the next LLM round must see agent_delegate."""
    session = kit_session_manager.get_session("web", "browser", "kit")
    agent = session.agent
    agent._execute_tool(
        "plan_present",
        {"goal": "Run tests", "steps": [{"id": "s1", "agent_id": "tester-1", "action": "Run suite"}]},
    )

    seen_tools = []

    class _RecordingCompletions(_FakeCompletions):
        async def create(self, **kwargs):
            names = []
            for t in kwargs.get("tools") or []:
                names.append(t["function"]["name"])
            seen_tools.append(names)
            return await super().create(**kwargs)

    completions = _RecordingCompletions([
        _tool_call_round("plan_approve", {}),
        _text_round("Approved. Starting with the tester."),
    ])
    agent.client = _FakeClient(completions)
    agent._max_context_tokens = 120_000

    async for _ in agent.chat_stream("looks good, approve it"):
        pass

    assert completions.calls == 2
    assert "agent_delegate" not in seen_tools[0]
    assert "agent_delegate" in seen_tools[1]
    assert session.mode == "orchestrate"


@pytest.mark.asyncio
async def test_orchestrate_delegate_round_trip(kit_session_manager):
    """Kit in orchestrate mode can agent_delegate to a teammate."""
    tester = kit_session_manager.get_session("web", "browser", "tester-1")
    tester.agent.client = _FakeClient(_FakeCompletions([_text_round("All 12 tests passed.")]))

    kit = kit_session_manager.get_session("web", "browser", "kit")
    kit.agent._execute_tool(
        "plan_present",
        {"goal": "Verify", "steps": [{"id": "s1", "agent_id": "tester-1", "action": "Run suite"}]},
    )
    kit.agent._execute_tool("plan_approve", {})
    assert kit.mode == "orchestrate"
    assert "agent_delegate" in kit.agent.allowed_tools

    kit.agent.client = _FakeClient(_FakeCompletions([
        _tool_call_round("agent_delegate", {"agent_id": "tester-1", "task": "run the test suite"}),
        _text_round("Tester says all 12 tests passed."),
    ]))
    kit.agent._max_context_tokens = 120_000
    reply = await kit.agent.chat("go")
    assert "12 tests" in reply
    messages = kit_session_manager.get_messages(tester.session_id)
    assert any(m["role"] == "user" and m["content"] == "run the test suite" for m in messages)
