"""Parse Kit prose/table plans into plan_present payloads."""

from tools.plan import (
    extract_plan_from_model_output,
    looks_like_plan_draft,
    parse_plan_draft_from_text,
    plan_present,
    resolve_plan_goal,
)


SAMPLE = """
**Plan: Build a simple Joke-Teller Python app**

| Step | Task | Assigned teammate | Success criteria | Dependencies |
|------|------|-------------------|------------------|--------------|
| 1 | **Research** best ways to fetch jokes | `researcher-1` | Summary of options | none |
| 2 | **Design** the app architecture | `dev-1` | Design doc | 1 |
| 3 | **Implement** skeleton code | `dev-1` | Working program | 2 |

**Risks & Mitigations**

- Dependency on APIs: use env vars.

**Open Questions**

- Preferred UI?

Please review the plan and let me know if it meets your needs.
"""

# Draft with plan id / table but no usable Plan: title (old "User request" path).
NO_GOAL_SAMPLE = """
Plan id: plan_abc123

| Step | Task | Assigned teammate | Success criteria | Dependencies |
|------|------|-------------------|------------------|--------------|
| 1 | **Scaffold** a Ruby joke CLI | `dev-1` | App runs | none |
| 2 | **Add** joke fetching | `dev-1` | Prints a joke | 1 |

Please review the plan and let me know if it meets your needs.
"""


def test_looks_like_plan_draft():
    assert looks_like_plan_draft(SAMPLE)
    assert not looks_like_plan_draft("Just a normal reply about jokes.")


def test_parse_joke_table_plan():
    parsed = parse_plan_draft_from_text(SAMPLE)
    assert isinstance(parsed, dict)
    assert "Joke-Teller" in parsed["goal"]
    assert len(parsed["steps"]) == 3
    assert parsed["steps"][0]["agent_id"] == "researcher-1"
    assert parsed["steps"][1]["depends_on"] == ["1"]
    assert parsed["risks"]
    assert parsed["open_questions"]


def test_parse_then_plan_present(tmp_path):
    parsed = parse_plan_draft_from_text(SAMPLE)
    md = plan_present(workspace_dir=str(tmp_path), **parsed)
    assert not md.startswith("Error:")
    assert "researcher-1" in md
    assert "awaiting your approval" in md


def test_resolve_plan_goal_prefers_user_message():
    assert resolve_plan_goal(
        "User request",
        [{"action": "Scaffold a Ruby joke CLI"}],
        fallback_goal="write a joke telling app in ruby",
    ) == "write a joke telling app in ruby"


def test_resolve_plan_goal_falls_back_to_first_step():
    assert resolve_plan_goal(
        "",
        [{"action": "Scaffold a Ruby joke CLI"}],
    ) == "Scaffold a Ruby joke CLI"


def test_parse_draft_uses_user_message_when_no_goal():
    parsed = parse_plan_draft_from_text(
        NO_GOAL_SAMPLE,
        fallback_goal="write a joke telling app in ruby",
    )
    assert isinstance(parsed, dict)
    assert parsed["goal"] == "write a joke telling app in ruby"


def test_parse_draft_uses_first_step_when_no_goal_or_user_message():
    parsed = parse_plan_draft_from_text(NO_GOAL_SAMPLE)
    assert isinstance(parsed, dict)
    assert "Scaffold" in parsed["goal"]
    assert parsed["goal"] != "User request"


def test_extract_plan_passes_fallback_goal():
    parsed = extract_plan_from_model_output(
        NO_GOAL_SAMPLE,
        fallback_goal="Can you write a joke telling app in ruby",
    )
    assert parsed is not None
    assert parsed["goal"] == "write a joke telling app in ruby"
