"""Intent engine, with emphasis on the `thought`-capture path added in Phase 5."""

from segpilot.policy.intent import build_intent, stock_intent

GOAL = "Fix the failing tests in the ledger package"


def _msgs_with_thought(thought: str):
    """A session where the agent emits NO free-form reasoning (content empty)
    and instead carries its rationale in the tool call's `thought` argument --
    the Gemini-via-tool-calling shape that motivated the thought param."""
    return [
        {"role": "system", "content": "agent"},
        {"role": "user", "content": GOAL},
        {"role": "assistant", "content": "",   # <- no prose, like Gemini
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "read_file",
                                      "arguments": f'{{"thought": "{thought}", "file_path": "ledger/reconcile.py"}}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "def reconcile_totals(...): ..."},
    ]


def test_thought_becomes_the_intent_when_no_reasoning():
    thought = "checking whether reconcile sums signed_amount or raw amount"
    msgs = _msgs_with_thought(thought)
    intent = build_intent(
        msgs, tool_name="read_file",
        tool_args={"thought": thought, "file_path": "ledger/reconcile.py"},
    )
    assert intent.source == "thought"
    assert "signed_amount" in intent.text
    # qualified by where it is looking
    assert "reconcile.py" in intent.text


def test_thought_intent_differs_from_stock():
    """The whole point: with a thought, intent_only stops collapsing into stock."""
    thought = "checking the discount tier boundary comparison"
    msgs = _msgs_with_thought(thought)
    pilot = build_intent(
        msgs, tool_name="read_file",
        tool_args={"thought": thought, "file_path": "ledger/pricing.py"},
    )
    stock = stock_intent(msgs)
    assert stock.text == GOAL           # stock still uses the standing instruction
    assert pilot.text != stock.text     # segpilot now reflects current activity
    assert pilot.source == "thought"


def test_reasoning_prose_still_wins_when_present():
    """If a model DOES emit reasoning prose, that path still works; thought is a
    fallback for models that do not, not a replacement."""
    msgs = [
        {"role": "user", "content": GOAL},
        {"role": "assistant", "content": "Now I'll inspect the tax rate lookup."},
    ]
    intent = build_intent(msgs, tool_name=None, tool_args=None)
    assert intent.source == "assistant"
    assert "tax rate" in intent.text


def test_empty_thought_falls_through():
    """A blank thought must not be treated as signal."""
    msgs = _msgs_with_thought("")
    intent = build_intent(
        msgs, tool_name="read_file",
        tool_args={"thought": "", "file_path": "ledger/pricing.py"},
    )
    # falls back to tool_call_only (path) qualified by the goal
    assert intent.source in ("tool_call_only", "original")


def test_thought_not_leaked_into_stock_baseline():
    """stock_intent must stay faithful to Paritok's _extract_query regardless of
    thoughts present in the session."""
    msgs = _msgs_with_thought("some current sub-goal")
    assert stock_intent(msgs).text == GOAL
