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


class RefTaggingFakeStrategy:
    """Like FakeStrategy, but tags output the way the real hosted GPU does:
    [REF:<hex of content>] <head> -- so shadow-store tests can extract a real id."""

    def compress(self, content, *, query=None, kind=None, **kw):
        import hashlib
        sid = hashlib.sha256(content.encode()).hexdigest()[:16]
        head = content.split("\n", 1)[0]
        return f"[REF:{sid}] {head}"


def test_compress_segment_populates_shadow_store_on_fresh_compression():
    from segpilot.policy.shadow import ShadowStore

    shadow = ShadowStore()
    comp = SegpilotCompressor(_cfg(), cache=None, strategy=RefTaggingFakeStrategy(), shadow=shadow)
    compressed, cache_hit = comp.compress_segment(BIG_FILE, kind="file_read", intent="foo")

    assert cache_hit is False
    assert len(shadow) == 1
    sid = compressed.split("]")[0].removeprefix("[REF:")
    entry = shadow.get(sid)
    assert entry.original == BIG_FILE
    assert entry.kind == "file_read"


def test_compress_segment_repopulates_shadow_store_on_cache_hit():
    """A compression served from the (cross-session) cache must still land in
    THIS session's (in-memory, per-session) shadow store -- otherwise a live
    adaptive session that hits a warm cache could never resolve its own refs."""
    from segpilot.policy.cache import CompressionCache
    from segpilot.policy.shadow import ShadowStore
    import tempfile, os

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        cache = CompressionCache(path)
        strategy = RefTaggingFakeStrategy()

        # First "session": populates the disk cache, fresh shadow store.
        shadow_a = ShadowStore()
        comp_a = SegpilotCompressor(_cfg(), cache=cache, strategy=strategy, shadow=shadow_a)
        compressed, hit_a = comp_a.compress_segment(BIG_FILE, kind="file_read", intent="foo")
        assert hit_a is False
        assert len(shadow_a) == 1

        # Second "session": brand-new (empty) shadow store, but the cache is warm.
        shadow_b = ShadowStore()
        comp_b = SegpilotCompressor(_cfg(), cache=cache, strategy=strategy, shadow=shadow_b)
        compressed_2, hit_b = comp_b.compress_segment(BIG_FILE, kind="file_read", intent="foo")

        assert hit_b is True                    # served from cache, not re-compressed
        assert compressed_2 == compressed
        assert len(shadow_b) == 1                # but still resolvable in THIS session
        sid = compressed_2.split("]")[0].removeprefix("[REF:")
        assert shadow_b.get(sid).original == BIG_FILE
        cache.close()
    finally:
        os.unlink(path)


def test_compress_segment_without_shadow_store_does_not_error():
    comp = SegpilotCompressor(_cfg(), cache=None, strategy=RefTaggingFakeStrategy(), shadow=None)
    compressed, _ = comp.compress_segment(BIG_FILE, kind="file_read", intent="foo")
    assert compressed.startswith("[REF:")


# ---- adaptive arm ---------------------------------------------------------

def test_adaptive_starts_conservative_sends_no_kind_or_intent():
    """A fresh controller has seen nothing, so every bucket is conservative:
    the adaptive arm must behave exactly like stock at the very start."""
    import dataclasses
    from segpilot.policy.adaptive import AdaptiveController

    controller = AdaptiveController()
    adaptive_arm = dataclasses.replace(ARMS["adaptive"], controller=controller)
    fake = FakeStrategy()
    comp = SegpilotCompressor(_cfg(), cache=None, strategy=fake)

    comp.process(_session(), adaptive_arm)

    assert all(c["kind"] is None for c in fake.calls)
    assert all(not c["query"] for c in fake.calls)


def test_adaptive_records_compression_per_segment():
    import dataclasses
    from segpilot.policy.adaptive import AdaptiveController

    controller = AdaptiveController()
    adaptive_arm = dataclasses.replace(ARMS["adaptive"], controller=controller)
    comp = SegpilotCompressor(_cfg(), cache=None, strategy=FakeStrategy())

    outcome = comp.process(_session(), adaptive_arm)

    snap = controller.snapshot()
    total_observations = sum(b["observations"] for b in snap.values())
    assert total_observations == len(outcome.segments)


def test_adaptive_routes_a_bucket_once_escalated():
    """Pre-escalate the file_read bucket by hand (as if a prior segment of
    this kind had already earned it), then confirm a NEW file_read segment in
    this call is sent kind+intent, unlike a fresh/unescalated bucket."""
    import dataclasses
    from segpilot.policy.adaptive import AdaptiveController

    controller = AdaptiveController()
    for _ in range(5):
        controller.record_compression("file_read")   # escalates: 0/5 regret
    assert controller.should_route("file_read") is True
    assert controller.should_route("log_output") is False   # untouched bucket

    adaptive_arm = dataclasses.replace(ARMS["adaptive"], controller=controller)
    fake = FakeStrategy()
    comp = SegpilotCompressor(_cfg(), cache=None, strategy=fake)
    outcome = comp.process(_session(), adaptive_arm)

    by_kind = {s.tool_name: c for s, c in zip(outcome.segments, fake.calls)}
    # log_output segment (run_tests) stays conservative; file_read (read_file)
    # is routed, since its bucket was pre-escalated above.
    kinds_sent = [c["kind"] for c in fake.calls]
    assert None in kinds_sent            # the conservative (log_output) segment
    assert "file_read" in kinds_sent     # the routed (file_read) segment


def test_adaptive_segment_outcome_keeps_real_kind_for_telemetry():
    """Even when the controller withholds kind from Paritok, SegmentOutcome
    must still record the REAL classified kind -- otherwise regret couldn't
    be attributed back to the right bucket later, and reporting would lie
    about what kind of content this was."""
    import dataclasses
    from segpilot.policy.adaptive import AdaptiveController

    controller = AdaptiveController()   # fresh: everything conservative
    adaptive_arm = dataclasses.replace(ARMS["adaptive"], controller=controller)
    comp = SegpilotCompressor(_cfg(), cache=None, strategy=FakeStrategy())

    outcome = comp.process(_session(), adaptive_arm)

    # The log_output/file_read segments were correctly identified even though
    # nothing was actually routed to Paritok this way.
    real_kinds = {s.kind for s in outcome.segments}
    assert real_kinds == {"log_output", "file_read"}


def test_non_adaptive_arms_are_unaffected_by_controller_field_existing():
    """Adding `controller` to Arm must not change any existing arm's
    behaviour -- they all default to controller=None."""
    outcome, fake = _run("segpilot")
    assert [s.kind for s in outcome.segments] == ["log_output", "file_read"]
