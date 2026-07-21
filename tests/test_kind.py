"""Kind engine: tool identity must beat content sniffing."""

from segpilot.policy.kind import (
    ASSISTANT_THINKING,
    DIRECTORY_LISTING,
    FILE_OPERATION,
    FILE_READ,
    LOG_OUTPUT,
    TOOL_RESULT,
    build_tool_call_index,
    label_messages,
    label_segment,
    tagger_kinds,
)

CODE = (
    "import logging\n"
    "def reconcile_totals(entries):\n"
    "    total = 0\n"
    "    for e in entries:\n"
    "        total += e.amount\n"
    "    return total\n"
)
NUMBERED = "".join(f"{i + 1:6d}\t{l}\n" for i, l in enumerate(CODE.splitlines()))

TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "/workspace/tests/test_ledger.py", line 42, in test_reconcile\n'
    "    assert reconcile_totals(entries) == 100\n"
    "AssertionError: assert 97 == 100\n"
)


def _call(cid: str, name: str, args: str):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": cid, "type": "function", "function": {"name": name, "arguments": args}}
        ],
    }


def test_tool_index_extracts_name_and_args():
    msgs = [_call("c1", "read_file", '{"file_path":"src/ledger.py"}')]
    idx = build_tool_call_index(msgs)
    assert idx["c1"]["name"] == "read_file"
    assert idx["c1"]["args"]["file_path"] == "src/ledger.py"


def test_line_numbered_file_read_is_file_read_not_log_output():
    """The regression finding #9 describes: Paritok's conversation-aware path
    calls this log_output. Tool identity gets it right."""
    msgs = [
        _call("c1", "read_file", '{"file_path":"src/ledger.py"}'),
        {"role": "tool", "tool_call_id": "c1", "content": NUMBERED},
    ]
    labels = label_messages(msgs)
    assert labels[1].kind == FILE_READ
    assert labels[1].source == "tool_map"
    assert labels[1].file_path == "src/ledger.py"


def test_we_disagree_with_tagger_on_file_reads():
    """Guards the claim in docs/findings.md #9. If upstream fixes their
    classifier this test fails loudly and we update the finding."""
    msgs = [
        _call("c1", "read_file", '{"file_path":"src/ledger.py"}'),
        {"role": "tool", "tool_call_id": "c1", "content": NUMBERED},
    ]
    ours = label_messages(msgs)[1].kind
    theirs = tagger_kinds(msgs)
    assert ours == FILE_READ
    if theirs:  # tag_messages succeeded
        assert theirs[1] == LOG_OUTPUT, (
            "Upstream now classifies line-numbered reads differently; "
            "re-check docs/findings.md finding #9"
        )


def test_test_output_is_log_output_even_without_traceback():
    msgs = [
        _call("c1", "run_tests", '{"path":"tests/"}'),
        {"role": "tool", "tool_call_id": "c1", "content": "5 passed in 0.3s\n"},
    ]
    assert label_messages(msgs)[1].kind == LOG_OUTPUT


def test_traceback_is_log_output():
    msgs = [
        _call("c1", "run_tests", '{"path":"tests/"}'),
        {"role": "tool", "tool_call_id": "c1", "content": TRACEBACK},
    ]
    assert label_messages(msgs)[1].kind == LOG_OUTPUT


def test_edit_and_listing_and_grep_kinds():
    cases = [
        ("edit_file", FILE_OPERATION),
        ("list_dir", DIRECTORY_LISTING),
        ("grep", TOOL_RESULT),
    ]
    for tool, expected in cases:
        msgs = [_call("c1", tool, "{}"), {"role": "tool", "tool_call_id": "c1", "content": "x\n" * 10}]
        assert label_messages(msgs)[1].kind == expected, tool


def test_assistant_prose_is_thinking():
    msgs = [{"role": "assistant", "content": "Let me check the expiry logic."}]
    assert label_messages(msgs)[0].kind == ASSISTANT_THINKING


def test_unknown_tool_falls_back_to_sniffing():
    msgs = [
        _call("c1", "some_mcp_tool_we_never_saw", "{}"),
        {"role": "tool", "tool_call_id": "c1", "content": CODE},
    ]
    label = label_messages(msgs)[1]
    assert label.source == "content_sniff"
    assert label.kind == FILE_READ  # the sniffer that gets code right


def test_orphan_tool_result_does_not_crash():
    """A tool result whose call we never saw (truncated history)."""
    msgs = [{"role": "tool", "tool_call_id": "missing", "content": CODE}]
    assert label_segment(msgs[0], tool_index={}).source == "content_sniff"


def test_malformed_arguments_do_not_crash():
    msgs = [
        _call("c1", "read_file", "not-json{{{"),
        {"role": "tool", "tool_call_id": "c1", "content": NUMBERED},
    ]
    label = label_messages(msgs)[1]
    assert label.kind == FILE_READ
    assert label.file_path is None
