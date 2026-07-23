"""Layer 2 — the intent engine. This is SEGPILOT's core contribution.

WHY INTENT IS THE LEVER
-----------------------
Paritok's compressor is intent-conditioned. Its system prompt says so outright:
"USER INTENT — the agent's current task. Drives keep/drop." We measured the
effect on the hosted GPU (see `python -m segpilot.probe --query`): on one
263-token file holding three unrelated functions, intent was worth 4.4x on
tokens *and* decided which function survived.

    "why does authenticate_user reject valid passwords" ->  58 tok, kept auth
    "debug the KMS key rotation logic"                  ->  65 tok, kept rotation
    "fix the prometheus metrics export path"            ->  36 tok, kept metrics
    ""                                                  -> 160 tok, kept all three

So intent quality *is* compression quality. Getting it wrong does not merely
waste tokens -- it discards the code the agent was about to need, which is what
later shows up as an `expand_context` call.

WHAT STOCK PARITOK DOES
-----------------------
`_extract_query` (paritok/middleware/wrapper.py:278) walks user turns
newest-first and returns the first real text, then applies that single string to
every segment in the request. For a coding agent that text is the user's
*original instruction*.

That is a reasonable default for turn 1 and increasingly wrong afterwards. By
turn 40 the agent may be reading a traceback from a test run while the intent
still reads "add OAuth support to the login flow". Every tool result in that
request is compressed against a task the agent finished long ago.

WHAT WE DO INSTEAD
------------------
Intent is derived per segment from the agent's *current* activity. The ordering
below was not guessed -- it was corrected by experiment (`python -m bench.intent_ab`):

  1. Recent assistant reasoning -- the SEMANTIC signal. It states what the agent
     is trying to find out ("sessions expire early when clock skew is on").
  2. The latest user turn, if it differs from the original instruction.
  3. The tool call that produced this segment -- a POSITIONAL qualifier only.
  4. The original instruction, as a floor when nothing fresher exists.

WHAT WE GOT WRONG FIRST, AND WHY IT MATTERS
-------------------------------------------
Our first implementation ranked the tool call highest and prefixed every intent
with the original instruction, producing:

    "Add rate limiting to all public API endpoints — currently reading src/auth.py"

That scored *worse than stock*: relevant-region retention fell from 100% to 0%
and the function the agent was debugging was destroyed. Two distinct mistakes:

  * "reading src/auth.py" says WHERE, not WHAT. A path carries no signal about
    which part of the file matters, so it cannot steer keep/drop.
  * Leading with a stale goal actively steers the compressor WRONG. The
    compressor faithfully optimised for "rate limiting" and discarded the
    session-expiry code the agent actually needed.

So the standing goal is *dropped*, not demoted, whenever fresher signal exists.
A stale intent is worse than no intent: an empty intent compresses
conservatively and keeps everything, while a wrong intent confidently discards
the right thing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Injected reminder blocks are scaffolding, not task text. Paritok strips these
# too (its _SYSTEM_REMINDER); we mirror the behaviour so our intent is at least
# as clean as stock's.
_SYSTEM_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)

# Keep intents short. The compressor reads this as a steering hint, not as
# context to reason over, and a long intent dilutes the signal.
MAX_INTENT_CHARS = 240


def clean_text(text: str | None) -> str | None:
    if not text:
        return None
    stripped = _SYSTEM_REMINDER.sub("", text).strip()
    return stripped or None


def _text_of(content) -> str | None:
    """Pull plain text out of either a string or a content-block list."""
    if isinstance(content, str):
        return clean_text(content)
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                if t := clean_text(block.get("text", "")):
                    parts.append(t)
        return " ".join(parts) if parts else None
    return None


def _truncate(text: str, limit: int = MAX_INTENT_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def describe_tool_call(name: str, args: dict) -> str | None:
    """Render a tool call as a phrase describing why content was fetched.

    These read like what an engineer would say they are doing, because that is
    the register the compressor was trained on.
    """
    if not name:
        return None
    a = args or {}

    def first(*keys):
        for k in keys:
            if v := a.get(k):
                return str(v)
        return None

    path = first("file_path", "path", "filename", "notebook_path")
    pattern = first("pattern", "query", "regex")
    command = first("command", "cmd", "script")

    n = name.lower()
    if n in ("read", "read_file", "view", "open_file", "cat"):
        return f"reading {path}" if path else "reading a file"
    if n in ("edit", "edit_file", "write", "str_replace_editor", "apply_patch"):
        return f"editing {path}" if path else "editing a file"
    if n in ("bash", "shell", "run_command", "execute_bash", "terminal"):
        return f"running `{_truncate(command, 100)}`" if command else "running a command"
    if n in ("grep", "search", "ripgrep", "search_files", "codebase_search"):
        where = f" in {path}" if path else ""
        return f"searching for `{pattern}`{where}" if pattern else "searching the codebase"
    if n in ("glob", "list_dir", "ls", "find"):
        return f"listing {path}" if path else "listing files"
    # Unknown / MCP tool: the name plus its most identifying argument is still
    # a better hint than nothing.
    detail = path or pattern or command
    return f"{name} {_truncate(detail, 80)}" if detail else name


@dataclass
class Intent:
    """An intent plus where it came from, so decisions stay auditable."""

    text: str
    source: str          # "tool_call" | "assistant" | "user_recent" | "original" | "none"
    goal: str | None = None
    activity: str | None = None

    def __str__(self) -> str:
        return self.text


def extract_original_instruction(messages: list[dict]) -> str | None:
    """The user's first real instruction — the standing goal.

    Note this is the *first*, whereas Paritok's `_extract_query` returns the
    newest-first match. On a single-instruction session they coincide; on a
    session with follow-ups we treat the first as the goal and the latest as
    current activity, which is what the two actually are.
    """
    for msg in messages or []:
        if msg.get("role") == "user":
            if t := _text_of(msg.get("content")):
                return t
    return None


def extract_latest_user_turn(messages: list[dict]) -> str | None:
    for msg in reversed(messages or []):
        if msg.get("role") == "user":
            if t := _text_of(msg.get("content")):
                return t
    return None


def extract_recent_reasoning(messages: list[dict], *, window: int = 6) -> str | None:
    """The newest assistant prose within `window` messages of the end.

    This is where an agent states its current sub-goal ("Now I'll check whether
    the session store expires tokens early"), which is exactly the steering the
    compressor wants.
    """
    for msg in reversed((messages or [])[-window:]):
        if msg.get("role") == "assistant":
            if t := _text_of(msg.get("content")):
                return t
    return None


def build_intent(
    messages: list[dict],
    *,
    tool_name: str | None = None,
    tool_args: dict | None = None,
) -> Intent:
    """Compose the intent for one segment.

    `tool_name`/`tool_args` describe the call that produced the segment being
    compressed, when known. Everything else is derived from the conversation.
    """
    goal = extract_original_instruction(messages)
    latest = extract_latest_user_turn(messages)
    reasoning = extract_recent_reasoning(messages)
    activity = describe_tool_call(tool_name, tool_args or {}) if tool_name else None

    # The tool call's `thought` argument, when present, is the agent's own
    # one-line statement of why it is making this call -- the most direct and
    # reliable current-activity signal there is. It exists because some models
    # (Gemini via tool-calling among them) emit no free-form reasoning text: the
    # `content` field is empty and everything is in the tool call. Rather than
    # starve the intent engine, we ask each tool to carry a `thought`. This is a
    # standard ReAct-style rationale, applied identically to every arm.
    thought = None
    if tool_args:
        raw = tool_args.get("thought") or tool_args.get("reasoning")
        thought = clean_text(raw) if isinstance(raw, str) else None

    # Semantic signal first, most reliable to least. `thought` and free-form
    # reasoning say WHAT matters; a tool call alone only says WHERE, so it can
    # qualify an intent but never be one.
    if thought:
        source, current = "thought", _truncate(thought, 180)
    elif reasoning:
        source, current = "assistant", _truncate(reasoning, 180)
    elif latest and latest != goal:
        source, current = "user_recent", _truncate(latest, 180)
    elif activity and goal:
        # Only a path to go on. Pair it with the goal, which is still the best
        # available description of what matters, and mark it low-confidence.
        return Intent(
            text=_truncate(f"{_truncate(goal, 140)} — {activity}"),
            source="tool_call_only",
            goal=goal,
            activity=activity,
        )
    elif goal:
        return Intent(text=_truncate(goal), source="original", goal=goal)
    else:
        # Nothing to steer with. Empty is the honest answer: our probe shows an
        # empty intent compresses conservatively and keeps everything, which is
        # strictly safer than inventing a wrong one.
        return Intent(text="", source="none")

    # Deliberately NOT prefixed with `goal`. Once fresher signal exists the
    # standing goal is stale, and a stale intent steers the compressor into
    # discarding exactly the content the agent is working on. See the module
    # docstring for the measurement that established this.
    text = f"{current} ({activity})" if activity else current
    return Intent(text=_truncate(text), source=source, goal=goal, activity=activity)


def stock_intent(messages: list[dict]) -> Intent:
    """Reproduce Paritok's `_extract_query` exactly, for A/B comparison.

    Newest-first over user turns, first non-empty wins. This is the baseline
    every SEGPILOT number is quoted against, so it must stay faithful.
    """
    for msg in reversed(messages or []):
        if msg.get("role") != "user":
            continue
        if t := _text_of(msg.get("content")):
            return Intent(text=t, source="original")
    return Intent(text="", source="none")
