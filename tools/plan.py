"""
Plan tools - Kit presents, revises, and tracks structured work plans.

Implementation for workspace-aware calls is intercepted by
PersonalAssistant._execute_tool() so plans land under the agent's
workspace_dir. execute_tool() still needs stubs for routing.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import json
import re
import uuid

PLAN_STATUSES = frozenset({
    "pending", "approved", "running", "blocked", "failed",
    "completed", "rejected", "superseded", "cancelled",
})
STEP_STATUSES = frozenset({
    "pending", "running", "done", "failed", "needs_clarification", "cancelled",
})


def plans_dir(workspace_dir: str = "workspace") -> Path:
    path = Path(workspace_dir) / "plans"
    path.mkdir(parents=True, exist_ok=True)
    return path


def plan_path(plan_id: str, workspace_dir: str = "workspace") -> Path:
    return plans_dir(workspace_dir) / f"{plan_id}.json"


def load_plan(plan_id: str, workspace_dir: str = "workspace") -> Optional[Dict[str, Any]]:
    path = plan_path(plan_id, workspace_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def save_plan(plan: Dict[str, Any], workspace_dir: str = "workspace") -> None:
    path = plan_path(plan["id"], workspace_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, indent=2))


def latest_plan(
    workspace_dir: str = "workspace",
    statuses: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Most recently created plan, optionally filtered by status."""
    candidates = []
    for path in plans_dir(workspace_dir).glob("plan_*.json"):
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if statuses and data.get("status") not in statuses:
            continue
        candidates.append(data)
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.get("created_at") or "", reverse=True)
    return candidates[0]


def resolve_plan_id(
    plan_id: Optional[str],
    workspace_dir: str = "workspace",
    statuses: Optional[List[str]] = None,
) -> Optional[str]:
    if plan_id and str(plan_id).strip():
        return str(plan_id).strip()
    plan = latest_plan(workspace_dir, statuses=statuses)
    return plan["id"] if plan else None


def _known_step_ids(steps: List[dict]) -> set:
    return {str(s.get("id")) for s in steps if s.get("id") is not None}


def _clean_depends_on(depends_on: List[Any], known: set, self_id: str) -> List[str]:
    """Keep only real step ids. Drop table-row leftovers and unknown tokens."""
    cleaned: List[str] = []
    for raw in depends_on or []:
        token = str(raw or "").strip().strip("`").strip()
        if not token or token.lower() in ("none", "-", "—", "n/a"):
            continue
        if "|" in token or len(token) > 32:
            continue
        if token == self_id or token not in known:
            continue
        if token not in cleaned:
            cleaned.append(token)
    return cleaned


def sanitize_plan_dependencies(plan: Dict[str, Any]) -> bool:
    """Drop unsatisfiable depends_on entries. Returns True if the plan changed."""
    steps = plan.get("steps") or []
    known = _known_step_ids(steps)
    changed = False
    for step in steps:
        old = list(step.get("depends_on") or [])
        new = _clean_depends_on(old, known, str(step.get("id") or ""))
        if new != [str(d) for d in old]:
            step["depends_on"] = new
            changed = True
    return changed


def ready_steps(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    steps = plan.get("steps") or []
    known = _known_step_ids(steps)
    done = {s["id"] for s in steps if s.get("status") == "done"}
    ready = []
    for step in steps:
        status = step.get("status") or "pending"
        if status not in ("pending", "needs_clarification"):
            continue
        deps = _clean_depends_on(step.get("depends_on") or [], known, str(step.get("id") or ""))
        if all(d in done for d in deps):
            ready.append(step)
    return ready


def format_plan_markdown(plan: Dict[str, Any]) -> str:
    status = plan.get("status", "pending")
    status_note = {
        "pending": "awaiting your approval.",
        "approved": "approved — ready to orchestrate.",
        "running": "in progress.",
        "blocked": "blocked — needs clarification.",
        "failed": "failed — stopped after a step failure.",
        "completed": "completed.",
        "rejected": "rejected.",
        "superseded": "superseded by a newer plan.",
        "cancelled": "cancelled — in-flight work was stopped.",
    }.get(status, "")

    lines = [
        f"# Plan: {plan['goal']}",
        "",
        f"Status: **{status}**" + (f" — {status_note}" if status_note else ""),
        f"Plan id: `{plan['id']}`",
        "",
        "## Steps",
        "",
    ]
    for i, step in enumerate(plan.get("steps", []), start=1):
        deps = ", ".join(step["depends_on"]) if step.get("depends_on") else "none"
        step_status = step.get("status") or "pending"
        lines.append(
            f"{i}. **{step['agent_id']}** (`{step['id']}`, {step_status}): {step['action']}"
        )
        if step.get("success_criteria"):
            lines.append(f"   - Success: {step['success_criteria']}")
        lines.append(f"   - Depends on: {deps}")
        if step.get("result"):
            preview = str(step["result"]).strip().replace("\n", " ")
            if len(preview) > 200:
                preview = preview[:197] + "…"
            lines.append(f"   - Result: {preview}")
        if step.get("clarification"):
            lines.append(f"   - Clarification needed: {step['clarification']}")
        lines.append("")

    ready = ready_steps(plan)
    all_done = bool(plan.get("steps")) and all(
        s.get("status") == "done" for s in plan.get("steps", [])
    )
    if all_done and status not in ("completed", "rejected", "superseded", "cancelled"):
        lines.append("## Ready now")
        lines.append("")
        lines.append("All steps are done — call `plan_complete`.")
        lines.append("")
    elif status in ("approved", "running", "blocked") and ready:
        lines.append("## Ready now")
        lines.append("")
        for step in ready:
            lines.append(f"- `{step['id']}` → **{step['agent_id']}**: {step['action']}")
        lines.append("")

    risks = plan.get("risks") or []
    if risks:
        lines.append("## Risks")
        lines.append("")
        for r in risks:
            lines.append(f"- {r}")
        lines.append("")
    questions = plan.get("open_questions") or []
    if questions:
        lines.append("## Open questions")
        lines.append("")
        for q in questions:
            lines.append(f"- {q}")
        lines.append("")

    if status == "pending":
        lines.append(
            "Reply to approve, revise, or reject. Nothing will be delegated until you approve."
        )
    elif status in ("approved", "running"):
        lines.append(
            "Orchestrate ready steps with agent_delegate, then plan_step_update. "
            "Call plan_complete when every step is done."
        )
    elif status == "blocked":
        lines.append(
            "Resolve the clarification with the user, then re-delegate the blocked step "
            "and plan_step_update it to running/done."
        )
    elif status == "failed":
        lines.append(
            "A step failed. Report to the user, then plan_reject or revise via a new plan_present."
        )
    elif status == "cancelled":
        lines.append(
            "Plan cancelled. Mode returned to plan. Present a new plan if the user still wants the work."
        )
    return "\n".join(lines).strip() + "\n"


# Field aliases models invent when they don't follow the tool schema exactly.
_STEP_ID_ALIASES = ("id", "step_id", "stepId", "step")
_STEP_ACTION_ALIASES = (
    "action", "name", "description", "task", "title", "summary", "what", "work",
)
_STEP_AGENT_ALIASES = (
    "agent_id", "agentId", "agent", "assignee", "owner", "teammate", "role",
)
_STEP_DEPS_ALIASES = ("depends_on", "dependencies", "deps", "dependsOn", "after")
_GOAL_ALIASES = ("goal", "title", "objective", "summary", "name")
_STEPS_ALIASES = ("steps", "tasks", "plan_steps", "planSteps")
_RISKS_ALIASES = ("risks", "risk", "mitigations")
_QUESTIONS_ALIASES = (
    "open_questions", "openQuestions", "questions", "clarifications",
)


def _first_nonempty(raw: dict, keys: tuple) -> Any:
    for key in keys:
        if key not in raw:
            continue
        val = raw[key]
        if val is None:
            continue
        if isinstance(val, str) and not val.strip():
            continue
        return val
    return None


def _coerce_step_dict(raw: dict) -> dict:
    """Map common LLM field aliases onto canonical step keys."""
    if not isinstance(raw, dict):
        return {}
    step: Dict[str, Any] = {}
    sid = _first_nonempty(raw, _STEP_ID_ALIASES)
    if sid is not None:
        step["id"] = str(sid).strip()
    agent = _first_nonempty(raw, _STEP_AGENT_ALIASES)
    if agent is not None:
        step["agent_id"] = str(agent).strip()
    action = _first_nonempty(raw, _STEP_ACTION_ALIASES)
    if action is not None:
        step["action"] = str(action).strip()
    deps = _first_nonempty(raw, _STEP_DEPS_ALIASES)
    if deps is not None:
        step["depends_on"] = deps
    for key in ("success_criteria", "successCriteria", "success", "done_when"):
        if key in raw and raw[key] is not None:
            step["success_criteria"] = str(raw[key]).strip()
            break
    if raw.get("status") is not None:
        step["status"] = raw["status"]
    if raw.get("result") is not None:
        step["result"] = raw["result"]
    if raw.get("clarification") is not None:
        step["clarification"] = raw["clarification"]
    return step


def coerce_plan_present_args(raw: Any) -> Dict[str, Any]:
    """Normalize any model plan payload into plan_present kwargs.

    Supports:
    - Canonical tool args: {goal, steps[{agent_id, action, ...}]}
    - Nested: {plan: {...}, commentary: "..."}
    - Aliases: description/name→action, step_id→id, agent→agent_id, etc.
    """
    empty = {"goal": "", "steps": None, "risks": None, "open_questions": None}
    if not isinstance(raw, dict):
        return empty
    data = dict(raw)
    # Unwrap nested plan / data / payload envelopes.
    for envelope in ("plan", "data", "payload", "result"):
        nested = data.get(envelope)
        if isinstance(nested, dict) and (
            nested.get("goal")
            or nested.get("steps")
            or nested.get("tasks")
            or any(nested.get(k) for k in _GOAL_ALIASES)
        ):
            for key, value in nested.items():
                if key not in data or not data.get(key):
                    data[key] = value
            break
    for noise in ("commentary", "message", "note", "status"):
        data.pop(noise, None)

    goal = _first_nonempty(data, _GOAL_ALIASES)
    steps = _first_nonempty(data, _STEPS_ALIASES)
    if isinstance(steps, str):
        steps = _coerce_list(steps)
    if isinstance(steps, list):
        steps = [_coerce_step_dict(s) if isinstance(s, dict) else s for s in steps]
    risks = _first_nonempty(data, _RISKS_ALIASES)
    questions = _first_nonempty(data, _QUESTIONS_ALIASES)
    return {
        "goal": str(goal or "").strip(),
        "steps": steps,
        "risks": risks,
        "open_questions": questions,
    }


def _plan_kwargs_valid(coerced: Dict[str, Any]) -> bool:
    if not coerced.get("goal") or not coerced.get("steps"):
        return False
    if not isinstance(coerced["steps"], list) or not coerced["steps"]:
        return False
    for step in coerced["steps"]:
        if not isinstance(step, dict):
            return False
        if not str(step.get("agent_id") or "").strip():
            return False
        if not str(step.get("action") or "").strip():
            return False
    return True


def looks_like_plan_json(text: str) -> bool:
    """True if text looks like a JSON plan blob (complete or still streaming)."""
    if not text:
        return False
    stripped = str(text).lstrip()
    # Streaming prefix: starts with goal/plan JSON key.
    if re.search(
        r'(?s)^\s*(```(?:json)?\s*)?\{\s*"(?:goal|plan|title|objective|steps|tasks)"\s*:',
        stripped,
    ):
        return True
    # Or prose wrapping a plan object.
    if re.search(
        r'(?s)(?:```(?:json)?\s*)?\{\s*"(?:goal|plan|title|objective)"\s*:',
        stripped,
    ):
        return True
    return parse_plan_json_blob(stripped) is not None


def _iter_json_objects(text: str) -> List[Any]:
    """Yield JSON values found as whole text, fenced blocks, or brace slices."""
    candidates: List[str] = []
    stripped = str(text).strip()
    if stripped:
        candidates.append(stripped)
    for fence in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", str(text), flags=re.I):
        body = fence.group(1).strip()
        if body:
            candidates.append(body)
    # Brace-balanced scan for top-level objects.
    s = str(text)
    i = 0
    while i < len(s):
        if s[i] != "{":
            i += 1
            continue
        depth = 0
        in_str = False
        esc = False
        for j in range(i, len(s)):
            ch = s[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(s[i : j + 1])
                    i = j + 1
                    break
        else:
            break
        continue

    out: List[Any] = []
    seen: set[str] = set()
    for cand in candidates:
        if cand in seen:
            continue
        seen.add(cand)
        try:
            out.append(json.loads(cand))
        except json.JSONDecodeError:
            continue
    return out


_MAX_GOAL_LEN = 120
_GENERIC_GOALS = frozenset({"user request", "untitled", "untitled plan", "plan"})


def _clean_goal_text(text: str) -> str:
    """Collapse whitespace and truncate for use as a plan goal/title."""
    g = " ".join(str(text or "").split()).strip()
    if not g:
        return ""
    # Drop common chat openers so the goal reads as a task name.
    g = re.sub(
        r"^(please\s+)?(can you|could you|would you|will you)\s+",
        "",
        g,
        flags=re.I,
    ).strip()
    if len(g) > _MAX_GOAL_LEN:
        g = g[: _MAX_GOAL_LEN - 1].rstrip() + "…"
    return g


def resolve_plan_goal(
    goal: Optional[str] = None,
    steps: Optional[List[Any]] = None,
    fallback_goal: Optional[str] = None,
) -> str:
    """Pick a usable plan goal when the model omitted or genericized it.

    Priority: explicit goal → user message (fallback_goal) → first step action
    → "User request".
    """
    g = str(goal or "").strip()
    if g and g.lower() not in _GENERIC_GOALS:
        return g
    fb = _clean_goal_text(fallback_goal or "")
    if fb:
        return fb
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        action = _clean_goal_text(step.get("action") or "")
        if action:
            return action
    return g or "User request"


def parse_plan_json_blob(
    text: str,
    fallback_goal: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """If text contains a JSON plan (any common model shape), return kwargs."""
    if not text or not str(text).strip():
        return None
    for parsed in _iter_json_objects(text):
        if not isinstance(parsed, dict):
            continue
        coerced = coerce_plan_present_args(parsed)
        coerced["goal"] = resolve_plan_goal(
            coerced.get("goal"),
            coerced.get("steps") if isinstance(coerced.get("steps"), list) else None,
            fallback_goal=fallback_goal,
        )
        if _plan_kwargs_valid(coerced):
            return coerced
    return None


def extract_plan_from_model_output(
    text: str,
    fallback_goal: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Best-effort plan extraction across model output styles.

    Order:
    1. JSON blob / fenced JSON / nested {plan:...}
    2. Markdown prose/table draft

    fallback_goal is typically the user's latest message — used when the model
    omitted a real goal/title.
    """
    blob = parse_plan_json_blob(text, fallback_goal=fallback_goal)
    if blob:
        return blob
    if looks_like_plan_draft(text):
        parsed = parse_plan_draft_from_text(text, fallback_goal=fallback_goal)
        if isinstance(parsed, dict) and _plan_kwargs_valid(
            coerce_plan_present_args(parsed)
        ):
            return coerce_plan_present_args(parsed)
    return None


def _normalize_steps(parsed_steps: List[Any]) -> Union[List[dict], str]:
    normalized: List[dict] = []
    for i, raw in enumerate(parsed_steps, start=1):
        if not isinstance(raw, dict):
            return f"Error: step {i} must be an object with agent_id and action"
        raw = _coerce_step_dict(raw)
        agent_id = str(raw.get("agent_id") or "").strip()
        action = str(raw.get("action") or "").strip()
        if not agent_id or not action:
            return f"Error: step {i} requires 'agent_id' and 'action'"
        depends_on = raw.get("depends_on") or []
        if isinstance(depends_on, str):
            depends_on = _coerce_list(depends_on)
        if not isinstance(depends_on, list):
            return f"Error: step {i} 'depends_on' must be a list"
        status = str(raw.get("status") or "pending").strip()
        if status not in STEP_STATUSES:
            return f"Error: step {i} has invalid status '{status}'"
        step = {
            "id": str(raw.get("id") or f"s{i}"),
            "agent_id": agent_id,
            "action": action,
            "success_criteria": str(raw.get("success_criteria") or "").strip(),
            "depends_on": [str(d) for d in depends_on],
            "status": status,
        }
        if raw.get("result") is not None:
            step["result"] = str(raw.get("result"))
        if raw.get("clarification") is not None:
            step["clarification"] = str(raw.get("clarification"))
        normalized.append(step)
    known = _known_step_ids(normalized)
    for step in normalized:
        step["depends_on"] = _clean_depends_on(
            step.get("depends_on") or [], known, step["id"]
        )
    return normalized


def looks_like_plan_draft(text: str) -> bool:
    """True if text looks like a prose/table plan (not necessarily plan_present)."""
    if not text or not str(text).strip():
        return False
    t = str(text)
    has_plan_heading = bool(
        re.search(r"(?i)(\*\*|\#)\s*plan\s*:", t) or re.search(r"(?i)plan id:", t)
    )
    has_steps = bool(
        re.search(r"(?i)\|[^|\n]*step[^|\n]*\|", t)
        or re.search(r"(?im)^\s*\d+\.\s+\*\*[^*]+\*\*", t)
    )
    return has_plan_heading and has_steps


def parse_plan_draft_from_text(
    text: str,
    fallback_goal: Optional[str] = None,
) -> Union[Dict[str, Any], str]:
    """
    Parse a Kit prose plan (markdown table or numbered list) into
    plan_present kwargs: {goal, steps, risks, open_questions}.

    fallback_goal (usually the user message) fills in when the draft has no
    usable Plan: heading.
    """
    if not text or not str(text).strip():
        return "Error: empty plan text"
    text = str(text)

    goal = ""
    m = re.search(r"(?im)\*\*\s*Plan\s*:\s*(.+?)\*\*", text)
    if m:
        goal = m.group(1).strip()
    if not goal:
        m = re.search(r"(?im)^#\s*Plan\s*:\s*(.+)$", text)
        if m:
            goal = m.group(1).strip()
    if not goal:
        m = re.search(r"(?im)^#{1,3}\s*(.+)$", text)
        if m:
            goal = m.group(1).strip()

    steps: List[dict] = []
    # Markdown table: | n | task | `agent` | success | deps |
    for row in re.finditer(
        r"(?im)^\|\s*(\d+)\s*\|\s*(.*?)\s*\|\s*`?([A-Za-z0-9_-]+)`?\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|?\s*$",
        text,
    ):
        num, task, agent_id, success, deps = row.groups()
        task_l = task.lower().strip()
        if task_l in ("task", "------", "---") or set(task_l) <= {"-"}:
            continue
        if "step" == task_l or task_l.startswith("----"):
            continue
        task_clean = re.sub(r"\*\*", "", task).strip()
        if not task_clean or set(task_clean) <= {"-"}:
            continue
        dep_raw = (deps or "").strip()
        depends_on: List[str] = []
        # Next-row bleed (`| 2 | Design…`) is not a dependency list.
        if "|" in dep_raw:
            dep_raw = ""
        if dep_raw and dep_raw.lower() not in ("none", "-", "—", ""):
            for part in re.split(r"[,;]", dep_raw):
                part = part.strip().strip("`")
                if part and "|" not in part and len(part) <= 32:
                    depends_on.append(part)
        steps.append({
            "id": str(num),
            "agent_id": agent_id.strip(),
            "action": task_clean,
            "success_criteria": re.sub(r"\*\*", "", success or "").strip(),
            "depends_on": depends_on,
        })

    # Numbered list: 1. **agent** (`id`, status): action
    if not steps:
        for row in re.finditer(
            r"(?im)^\s*(\d+)\.\s+\*\*([^*]+)\*\*\s*(?:\(`([^`]+)`[^)]*\))?\s*:\s*(.+)$",
            text,
        ):
            num, agent_id, step_id, action = row.groups()
            steps.append({
                "id": (step_id or f"s{num}").strip(),
                "agent_id": agent_id.strip(),
                "action": action.strip(),
                "success_criteria": "",
                "depends_on": [],
            })

    if not steps:
        return "Error: could not parse any steps from plan text"

    goal = resolve_plan_goal(goal, steps, fallback_goal=fallback_goal)

    def _bullets_after(heading_pat: str) -> List[str]:
        hm = re.search(heading_pat, text, flags=re.I | re.M)
        if not hm:
            return []
        rest = text[hm.end():]
        stop = re.search(r"(?im)^(\*\*[A-Za-z].+\*\*|#{1,3}\s+\S)", rest)
        chunk = rest[: stop.start()] if stop else rest
        items = []
        for line in chunk.splitlines():
            line = line.strip()
            if line.startswith(("-", "*", "•")):
                item = re.sub(r"^[-*•]\s*", "", line)
                item = item.strip().strip("*").strip()
                if item:
                    items.append(item)
        return items

    risks = _bullets_after(r"(?im)^\*?\*?Risks?[^\n]*\*?\*?")
    questions = _bullets_after(r"(?im)^\*?\*?Open questions?[^\n]*\*?\*?")

    return {
        "goal": goal,
        "steps": steps,
        "risks": risks,
        "open_questions": questions,
    }


def plan_present(
    goal: str,
    steps: Union[List[dict], str],
    risks: Optional[Union[List[str], str]] = None,
    open_questions: Optional[Union[List[str], str]] = None,
    workspace_dir: str = "workspace",
) -> str:
    """Persist a pending plan and return markdown for the user."""
    parsed_steps = _coerce_list(steps)
    if not goal or not str(goal).strip():
        return "Error: plan_present requires a non-empty 'goal'"
    if not parsed_steps:
        return "Error: plan_present requires at least one step"

    normalized = _normalize_steps(parsed_steps)
    if isinstance(normalized, str):
        return normalized

    # Supersede any still-active plans so only one is current.
    for active in ("pending", "approved", "running", "blocked"):
        while True:
            old = latest_plan(workspace_dir, statuses=[active])
            if not old:
                break
            old["status"] = "superseded"
            old["updated_at"] = datetime.now(timezone.utc).isoformat()
            save_plan(old, workspace_dir)

    plan = {
        "id": f"plan_{uuid.uuid4().hex[:12]}",
        "status": "pending",
        "goal": str(goal).strip(),
        "steps": normalized,
        "risks": _as_str_list(risks),
        "open_questions": _as_str_list(open_questions),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    save_plan(plan, workspace_dir)
    return format_plan_markdown(plan)


def plan_get(
    plan_id: Optional[str] = None,
    workspace_dir: str = "workspace",
) -> str:
    resolved = resolve_plan_id(
        plan_id,
        workspace_dir,
        statuses=["pending", "approved", "running", "blocked", "completed"],
    )
    if not resolved:
        return "Error: no plan found"
    plan = load_plan(resolved, workspace_dir)
    if not plan:
        return f"Error: plan '{resolved}' not found"
    return format_plan_markdown(plan)


def plan_approve(
    plan_id: Optional[str] = None,
    workspace_dir: str = "workspace",
) -> str:
    resolved = resolve_plan_id(plan_id, workspace_dir, statuses=["pending"])
    if not resolved:
        return "Error: no pending plan to approve"
    plan = load_plan(resolved, workspace_dir)
    if not plan:
        return f"Error: plan '{resolved}' not found"
    if plan.get("status") != "pending":
        return f"Error: plan '{resolved}' is {plan.get('status')}, not pending"

    plan["status"] = "approved"
    plan["updated_at"] = datetime.now(timezone.utc).isoformat()
    if sanitize_plan_dependencies(plan):
        plan["updated_at"] = datetime.now(timezone.utc).isoformat()
    for step in plan.get("steps", []):
        step.setdefault("status", "pending")
    save_plan(plan, workspace_dir)

    md = format_plan_markdown(plan)
    return (
        md
        + "\nMode switched to **orchestrate**. "
        "Delegate ready steps with agent_delegate (include prior step results "
        "in the task). After each reply, call plan_step_update. "
        "If a teammate needs clarification, set status needs_clarification, "
        "ask the user, then re-delegate. Call plan_complete when finished.\n"
    )


def plan_reject(
    plan_id: Optional[str] = None,
    reason: Optional[str] = None,
    workspace_dir: str = "workspace",
) -> str:
    resolved = resolve_plan_id(
        plan_id,
        workspace_dir,
        statuses=["pending", "approved", "running", "blocked", "failed"],
    )
    if not resolved:
        return "Error: no active plan to reject"
    plan = load_plan(resolved, workspace_dir)
    if not plan:
        return f"Error: plan '{resolved}' not found"

    plan["status"] = "rejected"
    plan["updated_at"] = datetime.now(timezone.utc).isoformat()
    if reason:
        plan["reject_reason"] = str(reason).strip()
    save_plan(plan, workspace_dir)
    note = f" Rejected: {reason}" if reason else ""
    return format_plan_markdown(plan) + f"\nMode returned to **plan**.{note}\n"


def plan_revise(
    plan_id: Optional[str] = None,
    goal: Optional[str] = None,
    steps: Optional[Union[List[dict], str]] = None,
    risks: Optional[Union[List[str], str]] = None,
    open_questions: Optional[Union[List[str], str]] = None,
    workspace_dir: str = "workspace",
) -> str:
    resolved = resolve_plan_id(plan_id, workspace_dir, statuses=["pending"])
    if not resolved:
        return "Error: no pending plan to revise"
    plan = load_plan(resolved, workspace_dir)
    if not plan:
        return f"Error: plan '{resolved}' not found"
    if plan.get("status") != "pending":
        return f"Error: plan '{resolved}' is {plan.get('status')}; only pending plans can be revised"

    if goal is not None and str(goal).strip():
        plan["goal"] = str(goal).strip()
    if steps is not None:
        parsed = _coerce_list(steps)
        if not parsed:
            return "Error: plan_revise steps must be a non-empty list"
        normalized = _normalize_steps(parsed)
        if isinstance(normalized, str):
            return normalized
        plan["steps"] = normalized
    if risks is not None:
        plan["risks"] = _as_str_list(risks)
    if open_questions is not None:
        plan["open_questions"] = _as_str_list(open_questions)

    plan["status"] = "pending"
    plan["updated_at"] = datetime.now(timezone.utc).isoformat()
    save_plan(plan, workspace_dir)
    return format_plan_markdown(plan)


def plan_step_update(
    step_id: str,
    status: str,
    plan_id: Optional[str] = None,
    result: Optional[str] = None,
    clarification: Optional[str] = None,
    workspace_dir: str = "workspace",
) -> str:
    if not step_id or not str(step_id).strip():
        return "Error: plan_step_update requires step_id"
    status = str(status or "").strip()
    if status not in STEP_STATUSES:
        return f"Error: invalid step status '{status}'. Use one of: {', '.join(sorted(STEP_STATUSES))}"

    resolved = resolve_plan_id(
        plan_id,
        workspace_dir,
        statuses=["approved", "running", "blocked", "failed"],
    )
    if not resolved:
        return "Error: no approved/running plan to update"
    plan = load_plan(resolved, workspace_dir)
    if not plan:
        return f"Error: plan '{resolved}' not found"

    target = None
    for step in plan.get("steps", []):
        if step["id"] == str(step_id).strip():
            target = step
            break
    if target is None:
        return f"Error: step '{step_id}' not found in plan '{resolved}'"

    target["status"] = status
    if result is not None:
        target["result"] = str(result)
    if clarification is not None:
        target["clarification"] = str(clarification)
    elif status != "needs_clarification":
        target.pop("clarification", None)

    plan["updated_at"] = datetime.now(timezone.utc).isoformat()
    statuses = [s.get("status", "pending") for s in plan.get("steps", [])]
    if "failed" in statuses:
        plan["status"] = "failed"
        plan["failure"] = f"Step {step_id} failed"
    elif "needs_clarification" in statuses:
        plan["status"] = "blocked"
    else:
        plan["status"] = "running"
        plan.pop("failure", None)

    save_plan(plan, workspace_dir)
    return format_plan_markdown(plan)


def plan_cancel(
    plan_id: Optional[str] = None,
    reason: Optional[str] = None,
    workspace_dir: str = "workspace",
) -> str:
    """Cancel an active plan; mark unfinished steps cancelled. Returns to plan mode."""
    resolved = resolve_plan_id(
        plan_id,
        workspace_dir,
        statuses=["pending", "approved", "running", "blocked", "failed"],
    )
    if not resolved:
        return "Error: no active plan to cancel"
    plan = load_plan(resolved, workspace_dir)
    if not plan:
        return f"Error: plan '{resolved}' not found"

    plan["status"] = "cancelled"
    plan["updated_at"] = datetime.now(timezone.utc).isoformat()
    if reason:
        plan["cancel_reason"] = str(reason).strip()
    for step in plan.get("steps", []):
        status = step.get("status") or "pending"
        if status not in ("done", "failed", "cancelled"):
            step["status"] = "cancelled"
            if not step.get("result"):
                step["result"] = "Cancelled by user"
    save_plan(plan, workspace_dir)
    note = f" Reason: {reason}" if reason else ""
    return format_plan_markdown(plan) + f"\nPlan cancelled. Mode returned to **plan**.{note}\n"


def plan_complete(
    plan_id: Optional[str] = None,
    summary: Optional[str] = None,
    workspace_dir: str = "workspace",
) -> str:
    resolved = resolve_plan_id(
        plan_id,
        workspace_dir,
        statuses=["approved", "running", "blocked"],
    )
    if not resolved:
        return "Error: no active plan to complete"
    plan = load_plan(resolved, workspace_dir)
    if not plan:
        return f"Error: plan '{resolved}' not found"

    unfinished = [
        s["id"] for s in plan.get("steps", [])
        if s.get("status") != "done"
    ]
    if unfinished:
        return (
            f"Error: cannot complete plan — unfinished steps: {', '.join(unfinished)}. "
            "Mark them done with plan_step_update, or plan_reject to abandon."
        )

    plan["status"] = "completed"
    plan["updated_at"] = datetime.now(timezone.utc).isoformat()
    if summary:
        plan["summary"] = str(summary).strip()
    save_plan(plan, workspace_dir)
    md = format_plan_markdown(plan)
    return md + "\nPlan completed. Mode returned to **plan**.\n"


def _coerce_list(value: Any) -> List[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value] if value.strip() else []
        return parsed if isinstance(parsed, list) else []
    return []


def _as_str_list(value: Any) -> List[str]:
    items = _coerce_list(value)
    return [str(i).strip() for i in items if str(i).strip()]


_STEP_ITEM = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "agent_id": {
            "type": "string",
            "description": "Team member id from YOUR TEAM",
        },
        "action": {"type": "string"},
        "success_criteria": {"type": "string"},
        "depends_on": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["agent_id", "action"],
}

PLAN_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "plan_present",
            "description": (
                "Present a structured work plan to the user and stop. "
                "Does not execute anything. Use this as the last action on "
                "every new user request that needs work from the team."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": "One-sentence statement of what the user wants",
                    },
                    "steps": {
                        "type": "array",
                        "description": (
                            "Ordered steps. Each step has agent_id, action, "
                            "optional success_criteria, and optional depends_on "
                            "(list of step ids)."
                        ),
                        "items": _STEP_ITEM,
                    },
                    "risks": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "open_questions": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["goal", "steps"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_get",
            "description": "Show the current or specified plan, including ready steps.",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {
                        "type": "string",
                        "description": "Plan id (default: most recent active plan)",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_approve",
            "description": (
                "Approve a pending plan after the user agrees. Switches to "
                "orchestrate mode so you can agent_delegate ready steps."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {
                        "type": "string",
                        "description": "Plan id (default: latest pending plan)",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_revise",
            "description": (
                "Update a pending plan from user feedback, then show it again. "
                "Stays in plan mode until the user approves."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string"},
                    "goal": {"type": "string"},
                    "steps": {
                        "type": "array",
                        "items": _STEP_ITEM,
                    },
                    "risks": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "open_questions": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_reject",
            "description": "Reject or abandon the current plan. Returns to plan mode.",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string"},
                    "reason": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_step_update",
            "description": (
                "Update a step while orchestrating: running, done, failed, or "
                "needs_clarification. Pass the teammate's reply in result."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string"},
                    "step_id": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": sorted(STEP_STATUSES),
                    },
                    "result": {
                        "type": "string",
                        "description": "Teammate output or failure detail",
                    },
                    "clarification": {
                        "type": "string",
                        "description": "Question for the user when status is needs_clarification",
                    },
                },
                "required": ["step_id", "status"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_complete",
            "description": (
                "Mark the plan completed after every step is done. Returns to plan mode."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string"},
                    "summary": {
                        "type": "string",
                        "description": "Short summary for the user",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_cancel",
            "description": (
                "Cancel the current plan and stop remaining work. Marks unfinished "
                "steps cancelled and returns to plan mode. Prefer the UI Cancel "
                "button when the user wants to abort in-flight work."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string"},
                    "reason": {"type": "string"},
                },
            },
        },
    },
]

PLAN_TOOL_FUNCTIONS = {
    "plan_present": plan_present,
    "plan_get": plan_get,
    "plan_approve": plan_approve,
    "plan_revise": plan_revise,
    "plan_reject": plan_reject,
    "plan_step_update": plan_step_update,
    "plan_complete": plan_complete,
    "plan_cancel": plan_cancel,
}
