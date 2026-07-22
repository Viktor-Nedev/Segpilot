"""Compressor arm logic, exercised without touching the network."""

from segpilot.compressor import ARMS, SegpilotCompressor
from segpilot.config import SegpilotConfig

BIG_FILE = "".join(
    f"def helper_function_number_{i}(alpha, beta):\n"
    f'    """Compute something for /workspace/src/module_{i}.py."""\n'
    f"    result = alpha * beta + {i}\n"
    f"    return result\n\n"
    for i in range(40)
)

TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "/workspace/tests/test_ledger.py", line 42, in test_reconcile\n'
    "    assert reconcile_totals(entries) == 100\n"
    "AssertionError: assert 97 == 100\n"
) * 12


class FakeStrategy:
    """Records how it was called and returns a deterministic 'compression'.

    Output depends on kind and intent so that arm differences are observable
    without a GPU — mirroring the real model, where both parameters matter.
    """

    def __init__(self):
        self.calls = []

    def compress(self, content, *, query=None, kind=None, **kw):
        self.calls.append({"content": content, "query": query, "kind": kind})
        # Keep the first line so must-keep spans partly survive; the tag encodes
        # the parameters so tests can assert what was passed.
        head = content.split("\n", 1)[0]
        return f"[{kind or 'nokind'}|{(query or 'nointent')[:20]}] {head}"


def _cfg():
    cfg = SegpilotConfig.load()
    cfg.paritok.api_key = "pk_live_test"
    cfg.policy.min_tokens = 50
    return cfg


def _session():
    return [
        {"role": "system", "content": "You are a coding agent."},
        {"role": "user", "content": "Fix the failing reconcile test"},
        {
            "role": "assistant",
            "content": "Running the tests to see the failure.",
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": "run_tests", "arguments": '{"path":"tests/"}'}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": TRACEBACK},
        {
            "role": "assistant",
            "content": "The total is short by 3. Reading the ledger module now.",
            "tool_calls": [{"id": "c2", "type": "function",
                            "function": {"name": "read_file", "arguments": '{"file_path":"src/ledger.py"}'}}],
        },
        {"role": "tool", "tool_call_id": "c2", "content": BIG_FILE},
    ]


def _run(arm_name):
    fake = FakeStrategy()
    comp = SegpilotCompressor(_cfg(), cache=None, strategy=fake)
    outcome = comp.process(_session(), ARMS[arm_name])
    return outcome, fake


def test_only_tool_messages_are_compressed():
    outcome, fake = _run("segpilot")
    assert len(outcome.segments) == 2          # two tool results, nothing else
    assert len(fake.calls) == 2


def test_stock_arm_sends_no_kind_and_the_original_instruction():
    """Stock on the hosted path: kind is null, intent is the user's first turn."""
    outcome, fake = _run("stock")
    assert all(c["kind"] is None for c in fake.calls)
    assert all(c["query"] == "Fix the failing reconcile test" for c in fake.calls)
    assert all(s.kind is None for s in outcome.segments)


def test_kind_arm_labels_by_tool_identity():
    outcome, fake = _run("kind_only")
    kinds = [s.kind for s in outcome.segments]
    assert kinds == ["log_output", "file_read"]
    # intent still stock
    assert all(c["query"] == "Fix the failing reconcile test" for c in fake.calls)


def test_intent_arm_uses_current_activity_not_original_instruction():
    outcome, _ = _run("intent_only")
    intents = [s.intent for s in outcome.segments]
    assert all(i != "Fix the failing reconcile test" for i in intents)
    # the file read should be steered by the reasoning that preceded it
    assert "short by 3" in intents[1]
    assert outcome.segments[1].intent_source == "assistant"


def test_intent_never_sees_the_future():
    """A segment must not be steered by reasoning produced after it.

    If this regressed, later arms would look better for a bogus reason.
    """
    outcome, _ = _run("segpilot")
    first = outcome.segments[0]
    assert "ledger module" not in first.intent


def test_segpilot_arm_combines_both():
    outcome, fake = _run("segpilot")
    assert [s.kind for s in outcome.segments] == ["log_output", "file_read"]
    assert "short by 3" in fake.calls[1]["query"]


def test_small_segments_are_skipped_not_compressed():
    cfg = _cfg()
    cfg.policy.min_tokens = 10_000        # nothing qualifies
    fake = FakeStrategy()
    comp = SegpilotCompressor(cfg, cache=None, strategy=fake)
    outcome = comp.process(_session(), ARMS["segpilot"])
    assert fake.calls == []
    assert all(s.skipped == "below_min_tokens" for s in outcome.segments)
    assert outcome.saved_tokens == 0


def test_guard_restores_original_when_retention_collapses():
    class Destroyer:
        def compress(self, content, *, query=None, kind=None, **kw):
            return "everything was thrown away"

    cfg = _cfg()
    cfg.policy.min_retention = 0.9
    comp = SegpilotCompressor(cfg, cache=None, strategy=Destroyer())
    outcome = comp.process(_session(), ARMS["segpilot+guard"])
    assert all(s.guard_tripped for s in outcome.segments)
    # Guard restores the original, so nothing is saved -- that is the point.
    assert outcome.saved_tokens == 0
    assert outcome.segments[1].compressed.startswith("def helper_function_number_0")


def test_guard_off_lets_destructive_compression_through():
    class Destroyer:
        def compress(self, content, *, query=None, kind=None, **kw):
            return "everything was thrown away"

    comp = SegpilotCompressor(_cfg(), cache=None, strategy=Destroyer())
    outcome = comp.process(_session(), ARMS["segpilot"])
    assert not any(s.guard_tripped for s in outcome.segments)
    assert outcome.saved_tokens > 0
    assert outcome.segments[1].retention < 0.5


def test_raw_arm_never_calls_the_model():
    """The reference-recording arm must not compress at all."""
    outcome, fake = _run("raw")
    assert fake.calls == []
    assert outcome.saved_tokens == 0
    assert all(s.skipped == "passthrough" for s in outcome.segments)
    # kind/intent are still computed (recorded for later analysis) but unused.
    assert [s.kind for s in outcome.segments] == [None, None]


def test_raw_arm_leaves_content_byte_identical():
    fake = FakeStrategy()
    comp = SegpilotCompressor(_cfg(), cache=None, strategy=fake)
    original = _session()
    rewritten, _ = comp.apply(original, ARMS["raw"])
    assert rewritten == original


def test_apply_rewrites_only_compressed_segments():
    comp = SegpilotCompressor(_cfg(), cache=None, strategy=FakeStrategy())
    original = _session()
    rewritten, outcome = comp.apply(original, ARMS["segpilot"])
    assert rewritten[0] == original[0]         # system untouched
    assert rewritten[3]["content"] != original[3]["content"]
    assert rewritten[3]["tool_call_id"] == "c1"   # metadata preserved
    assert outcome.compressed_count == 2


def test_cache_prevents_a_second_model_call():
    from segpilot.policy.cache import CompressionCache

    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        cache = CompressionCache(path)
        fake = FakeStrategy()
        comp = SegpilotCompressor(_cfg(), cache=cache, strategy=fake)
        comp.process(_session(), ARMS["segpilot"])
        assert len(fake.calls) == 2
        comp.process(_session(), ARMS["segpilot"])
        assert len(fake.calls) == 2            # served from cache
        assert cache.stats()["hits"] == 2
        cache.close()
    finally:
        os.unlink(path)


def test_cache_distinguishes_arms_unlike_paritoks():
    """The bug in finding #2: Paritok's cache would return the stock result here."""
    from segpilot.policy.cache import CompressionCache

    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        cache = CompressionCache(path)
        fake = FakeStrategy()
        comp = SegpilotCompressor(_cfg(), cache=cache, strategy=fake)
        stock = comp.process(_session(), ARMS["stock"])
        segp = comp.process(_session(), ARMS["segpilot"])
        assert len(fake.calls) == 4            # no false cache hits
        assert stock.segments[1].compressed != segp.segments[1].compressed
        cache.close()
    finally:
        os.unlink(path)
