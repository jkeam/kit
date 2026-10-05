"""Recover plan payloads across model output styles into plan_present markdown."""

import json

import pytest

from runtime.harmony import extract_harmony_tool_calls
from tools.plan import (
    coerce_plan_present_args,
    extract_plan_from_model_output,
    parse_plan_json_blob,
    plan_present,
)
from tests.test_plan import (
    _Chunk,
    _Choice,
    _Delta,
    _FakeClient,
    _FakeCompletions,
    kit_session_manager,  # noqa: F401 — pytest fixture
)

# Flat JSON with description/step_id (gpt-oss style)
JS_JOKE_BLOB_FLAT = json.dumps({
    "goal": "Build a simple JavaScript joke-teller application",
    "steps": [
        {
            "step_id": "1",
            "agent_id": "researcher-1",
            "description": "Research best practices for a lightweight JS joke app.",
            "success_criteria": "Summarize findings and recommend an approach.",
            "depends_on": [],
        },
        {
            "step_id": "2",
            "agent_id": "dev-1",
            "description": "Implement the application.",
            "success_criteria": "Application runs without errors.",
            "depends_on": ["1"],
        },
    ],
    "risks": ["External API rate limits"],
})

# Nested {plan, commentary}
JS_JOKE_BLOB = json.dumps({
    "plan": {
        "goal": "Create a JavaScript joke-teller application",
        "steps": [
            {
                "id": "1",
                "name": "Research best practices for JavaScript joke apps",
                "agent_id": "researcher",
                "parameters": {},
                "success_criteria": "Document findings",
                "dependencies": [],
            },
            {
                "id": "2",
                "name": "Implement the application",
                "agent_id": "dev-1",
                "parameters": {},
                "success_criteria": "App displays a joke on start",
                "dependencies": ["1"],
            },
        ],
        "risks": ["Unreliable joke API"],
        "open_questions": ["SPA or CLI?"],
    },
    "commentary": "Plan ready for approval.",
})

# Alternate aliases: agent/task/deps + fenced JSON with preamble
JS_JOKE_FENCED = """Sure — here's the plan:

```json
{
  "title": "JS joke app",
  "tasks": [
    {"step": "1", "agent": "researcher-1", "task": "Research joke APIs", "deps": []},
    {"step": "2", "agent": "dev-1", "task": "Build the app", "deps": ["1"]}
  ],
  "risk": ["Rate limits"]
}
```
"""

# Canonical tool-shaped args (models that follow the schema)
CANONICAL = {
    "goal": "Ship a JS joke app",
    "steps": [
        {"id": "s1", "agent_id": "dev-1", "action": "Write joke_app.js", "depends_on": []},
    ],
}


def test_coerce_flat_plan_with_description_and_step_id():
    coerced = coerce_plan_present_args(json.loads(JS_JOKE_BLOB_FLAT))
    assert coerced["goal"].startswith("Build a simple JavaScript")
    assert coerced["steps"][0]["action"].startswith("Research")
    assert coerced["steps"][0]["id"] == "1"
    assert coerced["steps"][1]["depends_on"] == ["1"]


def test_flat_plan_present(tmp_path):
    coerced = coerce_plan_present_args(json.loads(JS_JOKE_BLOB_FLAT))
    md = plan_present(workspace_dir=str(tmp_path), **coerced)
    assert md.startswith("# Plan:")
    assert "researcher-1" in md
    assert "{" not in md


def test_fenced_json_with_aliases():
    parsed = extract_plan_from_model_output(JS_JOKE_FENCED)
    assert parsed is not None
    assert parsed["goal"] == "JS joke app"
    assert parsed["steps"][0]["agent_id"] == "researcher-1"
    assert parsed["steps"][0]["action"].startswith("Research")
    assert parsed["steps"][1]["depends_on"] == ["1"]


def test_canonical_tool_args_still_work(tmp_path):
    coerced = coerce_plan_present_args(CANONICAL)
    md = plan_present(workspace_dir=str(tmp_path), **coerced)
    assert "Ship a JS joke app" in md
    assert "dev-1" in md


def test_markdown_draft_also_extracts():
    md = """
# Plan: Build a joke CLI

1. **researcher-1** (`1`, pending): Research APIs
2. **dev-1** (`2`, pending): Implement CLI
"""
    parsed = extract_plan_from_model_output(md)
    assert parsed is not None
    assert "joke" in parsed["goal"].lower()
    assert len(parsed["steps"]) == 2


@pytest.mark.asyncio
async def test_chat_stream_recovers_flat_json_plan_blob(kit_session_manager):
    session = kit_session_manager.get_session("web", "browser", "kit")
    agent = session.agent
    completions = _FakeCompletions([_json_blob_round(JS_JOKE_BLOB_FLAT)])
    agent.client = _FakeClient(completions)
    agent._max_context_tokens = 120_000

    events = []
    async for event in agent.chat_stream("write a javascript joke app"):
        events.append(event)

    leaked = "".join(e.get("content", "") for e in events if e["type"] == "text_delta")
    assert JS_JOKE_BLOB_FLAT not in leaked
    end = next(e for e in events if e["type"] == "stream_end")
    assert end["content"].startswith("# Plan:")
    assert "{" not in end["content"]


def test_coerce_wrapped_plan_args():
    coerced = coerce_plan_present_args(json.loads(JS_JOKE_BLOB))
    assert coerced["goal"].startswith("Create a JavaScript")
    assert len(coerced["steps"]) == 2
    assert coerced["steps"][0]["action"].startswith("Research")
    assert coerced["steps"][1]["depends_on"] == ["1"]


def test_parse_plan_json_blob():
    parsed = parse_plan_json_blob(JS_JOKE_BLOB)
    assert parsed is not None
    assert parsed["goal"]
    assert parsed["steps"]


def test_wrapped_plan_present(tmp_path):
    coerced = coerce_plan_present_args(json.loads(JS_JOKE_BLOB))
    md = plan_present(workspace_dir=str(tmp_path), **coerced)
    assert md.startswith("# Plan:")
    assert "researcher" in md
    assert "awaiting your approval" in md
    assert "{" not in md


def test_harmony_unwraps_nested_plan():
    text = (
        "<|start|>assistant<|channel|>commentary to=functions.plan_present "
        f"<|message|>{JS_JOKE_BLOB}<|call|>"
    )
    calls = extract_harmony_tool_calls(text)
    assert len(calls) == 1
    args = json.loads(calls[0]["function"]["arguments"])
    assert args["goal"].startswith("Create a JavaScript")
    assert args["steps"][0].get("action") or args["steps"][0].get("name")


def _json_blob_round(blob: str):
    return [
        _Chunk([_Choice(_Delta(content=blob))]),
        _Chunk([_Choice(_Delta(), finish_reason="stop")]),
    ]


@pytest.mark.asyncio
async def test_chat_stream_recovers_json_plan_blob(kit_session_manager):
    session = kit_session_manager.get_session("web", "browser", "kit")
    agent = session.agent
    completions = _FakeCompletions([_json_blob_round(JS_JOKE_BLOB)])
    agent.client = _FakeClient(completions)
    agent._max_context_tokens = 120_000

    events = []
    async for event in agent.chat_stream("write a javascript joke app"):
        events.append(event)

    leaked = "".join(e.get("content", "") for e in events if e["type"] == "text_delta")
    assert JS_JOKE_BLOB not in leaked
    assert any(e["type"] == "tool_call_start" and e["tool_name"] == "plan_present" for e in events)
    end = next(e for e in events if e["type"] == "stream_end")
    assert end["content"].startswith("# Plan:")
    assert "{" not in end["content"]
    assert session.active_plan_id


@pytest.mark.asyncio
async def test_chat_stream_recovers_fenced_json(kit_session_manager):
    session = kit_session_manager.get_session("web", "browser", "kit")
    agent = session.agent
    completions = _FakeCompletions([_json_blob_round(JS_JOKE_FENCED)])
    agent.client = _FakeClient(completions)
    agent._max_context_tokens = 120_000

    events = []
    async for event in agent.chat_stream("write a javascript joke app"):
        events.append(event)

    end = next(e for e in events if e["type"] == "stream_end")
    assert end["content"].startswith("# Plan:")
    assert "researcher-1" in end["content"]
