"""
Parse OpenAI Harmony (gpt-oss) tokens leaked into chat content.

gpt-oss models emit channel/tool-call markup like:
  <|start|>assistant<|channel|>commentary to=functions.plan_approve ...
  <|message|>{}<|call|>

OpenAI-compatible proxies sometimes fail to translate that into structured
tool_calls, so the raw tokens land in delta.content. This module recovers
tool calls and strips control tokens from user-visible text.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

# Any <|...|> control token (start, channel, message, call, return, etc.).
_SPECIAL_TOKEN_RE = re.compile(r"<\|[^|>]+\|>")

# Full tool-call envelope (with or without leading <|start|>...).
_CALL_BLOCK_RE = re.compile(
    r"(?:<\|start\|>(?P<header>.*?))?<\|message\|>(?P<body>.*?)<\|call\|>",
    re.DOTALL,
)

# final-channel user-facing answer
_FINAL_BLOCK_RE = re.compile(
    r"<\|channel\|>final\b[^<]*<\|message\|>(.*?)(?=<\|(?:end|return|start|call)\|>|$)",
    re.DOTALL,
)

# analysis-channel CoT (never show to users)
_ANALYSIS_BLOCK_RE = re.compile(
    r"<\|start\|>assistant<\|channel\|>analysis\b.*?<\|message\|>(.*?)(?=<\|(?:end|return|start|call)\|>|$)",
    re.DOTALL,
)

_RECIPIENT_RE = re.compile(r"\bto=(?:functions\.)?([A-Za-z_][A-Za-z0-9_]*)")
_IDENT_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\b")

_HEADER_NOISE = frozenset({
    "assistant", "user", "system", "developer", "tool",
    "commentary", "analysis", "final",
    "code", "json", "python", "browser", "functions",
    "constrain", "message", "start", "end", "call", "return",
    "to", "channel",
})


def looks_like_harmony(text: str) -> bool:
    """True if text contains Harmony control tokens."""
    return "<|" in text and "|>" in text


def _tool_name_from_header(header: str) -> Optional[str]:
    """Extract a function name from a Harmony message header."""
    if not header:
        return None

    # Prefer explicit recipients that aren't role names.
    for match in _RECIPIENT_RE.finditer(header):
        name = match.group(1)
        if name not in _HEADER_NOISE:
            return name

    # Malformed gpt-oss / proxy output, e.g.:
    #   <|channel|>commentary to=assistant plan_approve code
    # Scan remaining identifiers for a tool-like name.
    for match in _IDENT_RE.finditer(header):
        name = match.group(1)
        if name in _HEADER_NOISE:
            continue
        if "_" in name or name.endswith(("present", "approve", "reject", "update", "complete", "revise", "get", "list", "create", "delete", "search", "write", "read")):
            return name
        # Accept any remaining identifier that looks like a tool (snake_case-ish).
        if len(name) >= 3 and name.islower():
            return name
    return None


def _normalize_args(body: str) -> str:
    """Return a JSON-object string suitable for OpenAI tool_calls.arguments."""
    raw = (body or "").strip()
    if not raw:
        return "{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return "{}"
    if isinstance(parsed, dict):
        # Drop commentary-only payloads that aren't real tool args.
        if set(parsed.keys()) <= {"commentary"} and "plan_id" not in parsed:
            return "{}"
        # Nested / aliased plan payloads — normalize via tools.plan.
        try:
            from tools.plan import coerce_plan_present_args
            coerced = coerce_plan_present_args(parsed)
            if coerced.get("goal") and coerced.get("steps"):
                return json.dumps(coerced)
        except Exception:
            pass
        return json.dumps(parsed)
    return "{}"


def extract_harmony_tool_calls(text: str) -> List[Dict[str, Any]]:
    """
    Parse Harmony <|call|> blocks into OpenAI-style tool call dicts:
      {"id", "type", "function": {"name", "arguments"}}
    """
    if not looks_like_harmony(text):
        return []

    calls: List[Dict[str, Any]] = []
    for match in _CALL_BLOCK_RE.finditer(text):
        header = match.group("header") or ""
        # When <|start|> was omitted, header may be empty; look back for
        # channel/recipient text immediately before <|message|>.
        if not header:
            start = match.start()
            window = text[max(0, start - 200):start]
            header = window
        name = _tool_name_from_header(header)
        if not name:
            continue
        calls.append({
            "id": f"harmony_{uuid.uuid4().hex[:12]}",
            "type": "function",
            "function": {
                "name": name,
                "arguments": _normalize_args(match.group("body") or ""),
            },
        })
    return calls


def strip_harmony(text: str) -> str:
    """Remove Harmony control tokens / non-final channels; keep user-facing text."""
    if not text or not looks_like_harmony(text):
        return text

    finals = [m.group(1).strip() for m in _FINAL_BLOCK_RE.finditer(text)]
    finals = [f for f in finals if f]
    if finals:
        return "\n\n".join(finals)

    # Drop tool-call envelopes and analysis CoT entirely.
    cleaned = _CALL_BLOCK_RE.sub("", text)
    cleaned = _ANALYSIS_BLOCK_RE.sub("", cleaned)

    # Commentary preambles (no recipient) can be user-visible; keep their bodies.
    cleaned = re.sub(
        r"<\|start\|>assistant<\|channel\|>commentary\b(?![^<]*\bto=)[^<]*<\|message\|>",
        "",
        cleaned,
    )
    cleaned = _SPECIAL_TOKEN_RE.sub("", cleaned)
    # Collapse leftover header crumbs like "assistantcommentary".
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def parse_harmony_content(text: str) -> Tuple[str, List[Dict[str, Any]]]:
    """Return (visible_text, tool_calls) from possibly-Harmony model output."""
    if not text:
        return "", []
    if not looks_like_harmony(text):
        return text, []
    calls = extract_harmony_tool_calls(text)
    visible = strip_harmony(text)
    return visible, calls
