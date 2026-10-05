"""Team broadcast replies must not leak gpt-oss Harmony tool-call markup."""

from gateway.server import (
    _parse_broadcast_session_id,
    _prepare_broadcast_reply_text,
)


def test_parse_broadcast_session_id():
    assert _parse_broadcast_session_id("broadcast:web:anonymous") == ("web", "anonymous")
    assert _parse_broadcast_session_id("broadcast:cli:jkeam") == ("cli", "jkeam")
    assert _parse_broadcast_session_id("not-a-broadcast") == ("web", "anonymous")


def test_prepare_strips_plan_present_harmony():
    text = (
        "<|start|>assistant<|channel|>commentary to=plan_present "
        "<|constrain|>json<|message|>"
        '{"goal":"Build a CLI joke-telling app in Python",'
        '"steps":[{"name":"Research","assignee":"aid_researcher",'
        '"description":"Find APIs","tasks":["Search"]}],'
        '"success_criteria":"CLI runs",'
        '"risks":"API downtime",'
        '"open_questions":"API or local?"}'
        "<|call|>"
    )
    visible, escalate = _prepare_broadcast_reply_text(text)
    assert visible == ""
    assert escalate is True


def test_prepare_keeps_plain_chat():
    visible, escalate = _prepare_broadcast_reply_text("Looks fun — I'll handle the API layer.")
    assert visible == "Looks fun — I'll handle the API layer."
    assert escalate is False


def test_prepare_strips_think_tags():
    visible, escalate = _prepare_broadcast_reply_text(
        "<think>secret</think>Ship it."
    )
    assert visible == "Ship it."
    assert escalate is False
