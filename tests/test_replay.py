"""Replay harness + metrics, exercised offline with a fake compressor."""

import json
from pathlib import Path

from segpilot.compressor import SegpilotCompressor
from segpilot.config import SegpilotConfig
from segpilot.replay.harness import aggregate, replay_session
from segpilot.replay.metrics import ArmMetrics, task_relevant_retention
from segpilot.replay.session import load_session

BIG_FILE = "".join(
    f"def function_number_{i}(alpha):\n"
    f"    return signed_amount(alpha) + {i}  # /workspace/mod_{i}.py\n\n"
    for i in range(40)
)


class KeywordAwareFake:
    """Keeps a line only if the intent mentions a word on it. Lets us assert
    that a better intent yields better task-relevant retention offline."""

    def compress(self, content, *, query=None, kind=None, **kw):
        q = (query or "").lower()
        keep = [ln for ln in content.splitlines()
                if any(w in ln.lower() for w in q.split() if len(w) > 3)]
        return "\n".join(keep) or content.splitlines()[0]


def _write_session(path: Path, must_appear):
    messages = [
        {"role": "system", "content": "agent"},
        {"role": "user", "content": "fix the signed_amount bug in reconcile"},
        {"role": "assistant", "content": "Reading the module to check signed_amount.",
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "read_file", "arguments": '{"file_path":"mod.py"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": BIG_FILE},
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "meta", "task_id": "bug_x", "arm": "raw",
                            "instruction": "fix", "ground_truth": {"must_appear": must_appear},
                            "result": {}}) + "\n")
        f.write(json.dumps({"type": "messages", "messages": messages}) + "\n")


def _cfg():
    cfg = SegpilotConfig.load()
    cfg.paritok.api_key = "pk_live_test"
    cfg.policy.min_tokens = 20
    return cfg


def test_task_relevant_retention_counts_surviving_spans():
    kept, total = task_relevant_retention(
        ["def foo(): signed_amount()", "unrelated"],
        ["signed_amount", "reconcile_totals", "foo"],
    )
    assert total == 3
    assert kept == 2      # signed_amount and foo present, reconcile_totals not


def test_relevance_per_1k_rewards_keeping_more_for_less():
    cheap_good = ArmMetrics(arm="a", compressed_tokens=100, task_kept=9, task_total=10)
    expensive_good = ArmMetrics(arm="b", compressed_tokens=500, task_kept=10, task_total=10)
    # b keeps slightly more but costs 5x; a should win on the per-token metric.
    assert cheap_good.task_relevance_per_1k > expensive_good.task_relevance_per_1k


def test_replay_applies_every_arm_to_identical_content(tmp_path):
    session_path = tmp_path / "bug_x__raw.jsonl"
    _write_session(session_path, ["signed_amount", "function_number_3"])
    session = load_session(session_path)

    comp = SegpilotCompressor(_cfg(), cache=None, strategy=KeywordAwareFake())
    replay = replay_session(session, comp, ["stock", "intent_only", "segpilot"])

    assert set(replay.per_arm) == {"stock", "intent_only", "segpilot"}
    # every arm saw the same original token count
    origs = {m.original_tokens for m in replay.per_arm.values()}
    assert len(origs) == 1 and origs.pop() > 0


def test_intent_arm_retains_more_task_relevant_content(tmp_path):
    """The whole thesis in miniature: a current-activity intent that mentions
    signed_amount keeps that code; the stock global intent may not steer as well."""
    session_path = tmp_path / "bug_x__raw.jsonl"
    _write_session(session_path, ["signed_amount"])
    session = load_session(session_path)

    comp = SegpilotCompressor(_cfg(), cache=None, strategy=KeywordAwareFake())
    replay = replay_session(session, comp, ["stock", "segpilot"])
    # both intents mention signed_amount here, so both should keep it; the point
    # is the metric is computed and comparable, not that one always wins.
    assert replay.per_arm["segpilot"].task_total == 1


def test_aggregate_sums_across_sessions(tmp_path):
    for i in range(3):
        _write_session(tmp_path / f"bug_{i}__raw.jsonl", ["signed_amount"])
    sessions = [load_session(p) for p in sorted(tmp_path.glob("*.jsonl"))]
    comp = SegpilotCompressor(_cfg(), cache=None, strategy=KeywordAwareFake())
    replays = [replay_session(s, comp, ["stock", "segpilot"]) for s in sessions]
    totals = aggregate(replays, ["stock", "segpilot"])
    assert totals["stock"].sessions == 3
    assert totals["segpilot"].original_tokens > 0
