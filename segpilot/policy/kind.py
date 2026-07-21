"""Layer 1 — the kind engine.

WHY KIND MATTERS
----------------
`kind` is one of only two parameters the hosted GPU actually honours (see
`python -m segpilot.probe --kind`). It selects which system prompt the
compressor runs under, and the effect is large: on identical content we measured

    kind=file_read           -> 206 tokens
    kind=log_output          -> 187 tokens
    kind=assistant_thinking  ->  15 tokens

Stock Paritok sends `kind: null` on the hosted path. `local_model.py:163` falls
back to `classify_kind_from_content` when kind is None, but `gpu_server.py` has
no such fallback -- it forwards `None` verbatim. So every hosted-path tool
result is compressed with no kind at all.

WHY WE DON'T SNIFF CONTENT
--------------------------
Paritok ships two content sniffers, and they disagree with each other. On the
exact same line-numbered file read:

    classify_segment_kind + reclassify_tool_result  -> log_output   (wrong)
    classify_kind_from_content                      -> file_read    (right)

The first path -- the one `tag_messages` uses, and the one the tagger docstring
recommends for middleware use -- misfires because its `role == "tool"` branch
tests "head has more than 5 newlines -> log_output" before any code check. Any
multi-line file read trips it. And because it returns `log_output` rather than
`tool_result`, `reclassify_tool_result` short-circuits on its first line, so its
`file_read` rules (including the explicit Claude Code `cat -n` rule) are
unreachable in practice. See docs/findings.md finding #9.

We sidestep the disagreement entirely: **we know which tool produced each
result**, because the agent told us via `tool_call_id`. Tool identity is ground
truth; content sniffing is a guess made in its absence. So we map tool -> kind
directly and only fall back to sniffing for tools we do not recognise.

This is strictly more information than either Paritok path has, and it is
available to any agent framework using standard tool-calling.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from paritok.strategies.tagger import classify_kind_from_content, tag_messages

# Kinds the compressor was trained on (paritok/strategies/tagger.py).
FILE_READ = "file_read"
LOG_OUTPUT = "log_output"
FILE_OPERATION = "file_operation"
DIRECTORY_LISTING = "directory_listing"
FILE_EDIT_CONFIRM = "file_edit_confirm"
TOOL_RESULT = "tool_result"
ASSISTANT_THINKING = "assistant_thinking"

# Tool name -> kind. Names are matched case-insensitively and cover the common
# spellings across agent frameworks (Claude Code, Cursor, OpenHands, ours).
TOOL_KIND_MAP: dict[str, str] = {
    # reading source
    "read": FILE_READ, "read_file": FILE_READ, "view": FILE_READ,
    "open_file": FILE_READ, "cat": FILE_READ, "notebook_read": FILE_READ,
    # command / test output
    "bash": LOG_OUTPUT, "shell": LOG_OUTPUT, "run_command": LOG_OUTPUT,
    "execute_bash": LOG_OUTPUT, "terminal": LOG_OUTPUT, "run_tests": LOG_OUTPUT,
    "pytest": LOG_OUTPUT,
    # mutations
    "edit": FILE_OPERATION, "edit_file": FILE_OPERATION, "write": FILE_OPERATION,
    "str_replace_editor": FILE_OPERATION, "apply_patch": FILE_OPERATION,
    "create_file": FILE_OPERATION,
    # navigation
    "ls": DIRECTORY_LISTING, "list_dir": DIRECTORY_LISTING, "glob": DIRECTORY_LISTING,
    "find": DIRECTORY_LISTING,
    # search returns matches, not whole files
    "grep": TOOL_RESULT, "search": TOOL_RESULT, "ripgrep": TOOL_RESULT,
    "search_files": TOOL_RESULT, "codebase_search": TOOL_RESULT,
}


@dataclass
class SegmentLabel:
    """What we decided about one segment, and why."""

    kind: str
    source: str            # "tool_map" | "content_sniff" | "role"
    tool_name: str | None = None
    file_path: str | None = None

    def __str__(self) -> str:
        return self.kind


def _parse_args(raw) -> dict:
    """Tool arguments arrive as a JSON string in OpenAI format, dict elsewhere."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def build_tool_call_index(messages: list[dict]) -> dict[str, dict]:
    """Map tool_call_id -> {name, args} by walking assistant messages.

    Mirrors what Paritok's `_build_tool_use_index` (wrapper.py:327) does to
    recover a `source` path -- we reuse the same linkage for `kind`, which is
    the part they leave on the table.
    """
    index: dict[str, dict] = {}
    for msg in messages or []:
        if msg.get("role") != "assistant":
            continue
        for call in msg.get("tool_calls") or []:
            call_id = call.get("id")
            if not call_id:
                continue
            fn = call.get("function") or {}
            index[call_id] = {
                "name": (fn.get("name") or call.get("name") or "").strip(),
                "args": _parse_args(fn.get("arguments") or call.get("input")),
            }
    return index


def kind_for_tool(tool_name: str | None) -> str | None:
    if not tool_name:
        return None
    return TOOL_KIND_MAP.get(tool_name.strip().lower())


def label_segment(
    message: dict,
    *,
    tool_index: dict[str, dict] | None = None,
) -> SegmentLabel:
    """Decide the `kind` for one message.

    Priority: tool identity, then content sniffing, then role.
    """
    role = message.get("role")

    if role == "assistant":
        return SegmentLabel(kind=ASSISTANT_THINKING, source="role")

    if role == "tool":
        call = (tool_index or {}).get(message.get("tool_call_id") or "") or {}
        tool_name = call.get("name")
        args = call.get("args") or {}
        path = args.get("file_path") or args.get("path") or args.get("filename")

        if mapped := kind_for_tool(tool_name):
            return SegmentLabel(
                kind=mapped, source="tool_map", tool_name=tool_name, file_path=path
            )

        # Unknown tool: fall back to the sniffer that gets file reads right.
        content = message.get("content") or ""
        sniffed = classify_kind_from_content(content) if content else TOOL_RESULT
        return SegmentLabel(
            kind=sniffed, source="content_sniff", tool_name=tool_name, file_path=path
        )

    # system / user turns are protected by the compressor anyway.
    return SegmentLabel(kind=TOOL_RESULT, source="role")


def label_messages(messages: list[dict]) -> list[SegmentLabel]:
    """Label every message in a conversation."""
    index = build_tool_call_index(messages)
    return [label_segment(m, tool_index=index) for m in messages or []]


def stock_kind(_message: dict) -> None:
    """What stock Paritok sends on the hosted path: nothing.

    Exists so the replay harness can express the `stock` arm honestly rather
    than approximating it. `gpu_server.py` forwards `kind=None` verbatim
    because the middleware's tool-result call sites pass no kind and, unlike
    `local_model.py`, it has no sniffing fallback.
    """
    return None


def tagger_kinds(messages: list[dict]) -> list[str]:
    """Kinds as `tag_messages` would assign them — the comparison baseline.

    Used by the replay harness to quantify how often tool-identity mapping and
    Paritok's conversation-aware sniffing disagree, which is the evidence behind
    finding #9. We do not use this for production labelling.
    """
    try:
        return [lab["kind"] for lab in tag_messages(messages)]
    except Exception:  # noqa: BLE001 — comparison path must never break a run
        return []
