"""Tests for gpt-oss Harmony token parsing / stripping."""

import json

import pytest

from runtime.harmony import (
    extract_harmony_tool_calls,
    looks_like_harmony,
    parse_harmony_content,
    strip_harmony,
)
from tools.plan import load_plan
from tests.test_plan import (
    _FakeClient,
    _FakeCompletions,
    _text_round,
    _Chunk,
    _Choice,
    _Delta,
    kit_session_manager,  # noqa: F401 — pytest fixture
)


def test_looks_like_harmony():
    assert looks_like_harmony("<|start|>assistant<|message|>hi<|end|>")
    assert not looks_like_harmony("Just a normal reply")


def test_extract_standard_functions_recipient():
    text = (
        "<|start|>assistant<|channel|>commentary to=functions.plan_approve "
        "<|constrain|>json<|message|>{}<|call|>"
    )
    calls = extract_harmony_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "plan_approve"
    assert json.loads(calls[0]["function"]["arguments"]) == {}


def test_extract_malformed_to_assistant_plan_approve():
    """OpenCode Zen / gpt-oss sometimes emits this broken recipient form."""
    text = (
        '<|start|>assistant<|channel|>commentary to=assistant plan_approve code'
        '<|message|>{"commentary":"Plan approved."}<|call|>'
    )
    visible, calls = parse_harmony_content(text)
    assert visible == ""
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "plan_approve"
    assert json.loads(calls[0]["function"]["arguments"]) == {}


def test_strip_keeps_final_channel():
    text = (
        "<|start|>assistant<|channel|>analysis<|message|>secret CoT<|end|>"
        "<|start|>assistant<|channel|>final<|message|>Hello there.<|return|>"
    )
    assert strip_harmony(text) == "Hello there."


def test_strip_removes_call_block():
    text = (
        "Prefacing text\n"
        "<|start|>assistant<|channel|>commentary to=functions.read "
        '<|message|>{"path":"x"}<|call|>'
    )
    cleaned = strip_harmony(text)
    assert "<|" not in cleaned


def _harmony_text_round(text: str):
    return [
        _Chunk([_Choice(_Delta(content=text))]),
        _Chunk([_Choice(_Delta(), finish_reason="stop")]),
    ]


@pytest.mark.asyncio
async def test_chat_stream_recovers_harmony_plan_approve(kit_session_manager):
    session = kit_session_manager.get_session("web", "browser", "kit")
    agent = session.agent
    agent._execute_tool(
        "plan_present",
        {"goal": "Ship it", "steps": [{"id": "s1", "agent_id": "tester-1", "action": "Build"}]},
    )
    assert session.mode == "plan"

    harmony = (
        '<|start|>assistant<|channel|>commentary to=assistant plan_approve code'
        '<|message|>{"commentary":"Plan approved."}<|call|>'
    )
    completions = _FakeCompletions([
        _harmony_text_round(harmony),
        _text_round("Plan approved. Delegating next."),
    ])
    agent.client = _FakeClient(completions)
    agent._max_context_tokens = 120_000

    events = []
    async for event in agent.chat_stream("looks good, approve"):
        events.append(event)

    assert any(
        e["type"] == "tool_call_start" and e["tool_name"] == "plan_approve"
        for e in events
    )
    leaked = "".join(e.get("content", "") for e in events if e["type"] == "text_delta")
    assert "<|start|>" not in leaked
    assert "<|call|>" not in leaked
    end = next(e for e in events if e["type"] == "stream_end")
    assert "<|" not in end["content"]
    assert session.mode == "orchestrate"
    plan = load_plan(session.active_plan_id, str(agent.workspace_dir))
    assert plan["status"] == "approved"
