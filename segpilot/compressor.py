"""The compression core, shared by the live gateway and the offline replay harness.

Both paths run this same code. That is deliberate: if the live run and the
benchmark used different compression logic, the benchmark would not be measuring
the product.

WHY WE CALL GpuServerStrategy DIRECTLY
--------------------------------------
We use Paritok's own hosted-GPU client, but not `CompressionPipeline`. The
pipeline caches on content alone (finding #2), which would collapse every
benchmark arm into whichever ran first — the arms differ precisely in `kind` and
`query` on identical content. So we own the cache (`policy/cache.py`) and let
Paritok own the compression.

Everything that reaches the model still goes through Paritok's client, to
Paritok's hosted GPU, under the account's API key.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from paritok.strategies.gpu_server import GpuServerStrategy
from paritok.token_counter import count_tokens

from segpilot.config import SegpilotConfig
from segpilot.policy.cache import CompressionCache
from segpilot.policy.guard import score_retention
from segpilot.policy.intent import Intent, build_intent, stock_intent
from segpilot.policy.adaptive import AdaptiveController
from segpilot.policy.kind import build_tool_call_index, label_segment, stock_kind
from segpilot.policy.shadow import ShadowStore


@dataclass(frozen=True)
class Arm:
    """One benchmark configuration.

    `stock` reproduces today's Paritok behaviour on the hosted path: no kind,
    and one global intent from `_extract_query`. Every other arm turns on
    exactly one more thing, so the report can attribute the gain.
    """

    name: str
    use_kind: bool
    use_intent: bool
    use_guard: bool = False
    compress: bool = True   # False = passthrough; used to record neutral references
    # Set only for the "adaptive" arm, and only per live run (see agent/ruff.py) --
    # never on the shared ARMS template below, since AdaptiveController is
    # stateful and learns within a single session. When present, it overrides
    # use_kind/use_intent PER SEGMENT: use_kind/use_intent above become the
    # ceiling (what the controller may escalate to), not a fixed setting.
    controller: AdaptiveController | None = None


ARMS: dict[str, Arm] = {
    # Passthrough. Compression is off entirely, so an agent run under `raw` sees
    # full context and chooses its trajectory on complete information. Reference
    # sessions are recorded under this arm so the replayed comparison is not
    # biased by having been produced under one of the arms being compared.
    "raw":          Arm("raw",          use_kind=False, use_intent=False, compress=False),
    "stock":        Arm("stock",        use_kind=False, use_intent=False),
    "kind_only":    Arm("kind_only",    use_kind=True,  use_intent=False),
    "intent_only":  Arm("intent_only",  use_kind=False, use_intent=True),
    "segpilot":     Arm("segpilot",     use_kind=True,  use_intent=True),
    "segpilot+guard": Arm("segpilot+guard", use_kind=True, use_intent=True, use_guard=True),
    # Template only -- always used via dataclasses.replace(ARMS["adaptive"],
    # controller=AdaptiveController()) at the start of a live run. Regret
    # depends on the live model's actual behaviour, so this arm cannot be
    # offline-replayed and is deliberately excluded from COMPRESSING_ARMS.
    "adaptive":     Arm("adaptive",     use_kind=True,  use_intent=True, controller=None),
}

# The arms that actually compress — the set the replay harness sweeps and the
# report compares. `raw` is excluded because it is the recording substrate, not
# a policy under test.
COMPRESSING_ARMS: tuple[str, ...] = (
    "stock", "kind_only", "intent_only", "segpilot", "segpilot+guard",
)


@dataclass
class SegmentOutcome:
    """What happened to one segment. This is the telemetry record."""

    index: int
    kind: str | None
    intent: str
    intent_source: str
    original_tokens: int
    compressed_tokens: int
    retention: float
    compressed: str
    cache_hit: bool = False
    skipped: str | None = None      # reason, when we did not compress
    guard_tripped: bool = False
    tool_name: str | None = None
    file_path: str | None = None

    @property
    def saved_tokens(self) -> int:
        return self.original_tokens - self.compressed_tokens

    @property
    def ratio(self) -> float:
        if not self.original_tokens:
            return 1.0
        return self.compressed_tokens / self.original_tokens


@dataclass
class RequestOutcome:
    """Everything that happened to one request's worth of messages."""

    arm: str
    messages: list[dict]
    segments: list[SegmentOutcome] = field(default_factory=list)

    @property
    def original_tokens(self) -> int:
        return sum(s.original_tokens for s in self.segments)

    @property
    def compressed_tokens(self) -> int:
        return sum(s.compressed_tokens for s in self.segments)

    @property
    def saved_tokens(self) -> int:
        return self.original_tokens - self.compressed_tokens

    @property
    def ratio(self) -> float:
        if not self.original_tokens:
            return 1.0
        return self.compressed_tokens / self.original_tokens

    @property
    def compressed_count(self) -> int:
        return sum(1 for s in self.segments if not s.skipped)


class SegpilotCompressor:
    """Applies an arm's policy to a message list."""

    def __init__(
        self,
        config: SegpilotConfig,
        *,
        cache: CompressionCache | None = None,
        strategy=None,
        shadow: ShadowStore | None = None,
    ):
        self.config = config
        self.cache = cache
        # Injectable so tests can run without touching the network.
        self.strategy = strategy or GpuServerStrategy(config.to_paritok_config().gpu_server)
        # Populated by compress_segment when set. Lets the "adaptive" live arm's
        # expand_context tool resolve a [REF:id] back to its original -- see
        # segpilot/policy/shadow.py for why we need our own store rather than
        # Paritok's (server-side, tied to CompressionPipeline, which we bypass).
        self.shadow = shadow

    # ---- policy decisions ------------------------------------------------

    def decide_kind(self, message: dict, arm: Arm, tool_index: dict) -> tuple[str | None, str | None, str | None]:
        """Returns (kind, tool_name, file_path)."""
        if not arm.use_kind:
            return stock_kind(message), None, None
        label = label_segment(message, tool_index=tool_index)
        return label.kind, label.tool_name, label.file_path

    def decide_intent(
        self, messages: list[dict], upto: int, arm: Arm, tool_index: dict
    ) -> Intent:
        """Intent for the segment at `upto`.

        Only messages up to that point are visible, so a segment is never
        steered by reasoning the agent had not produced yet. Getting this wrong
        would leak future information into the benchmark and inflate our numbers.
        """
        history = messages[: upto + 1]
        if not arm.use_intent:
            return stock_intent(history)
        call = tool_index.get(messages[upto].get("tool_call_id") or "") or {}
        return build_intent(
            history, tool_name=call.get("name"), tool_args=call.get("args")
        )

    # ---- compression -----------------------------------------------------

    def compress_segment(
        self, content: str, *, kind: str | None, intent: str
    ) -> tuple[str, bool]:
        """Compress one segment. Returns (compressed, cache_hit)."""
        if self.cache is not None:
            if hit := self.cache.get(content, kind, intent):
                # Re-record into the shadow store even on a cache hit: the
                # store is per-session and in-memory (see shadow.py), so a
                # compression served from the (cross-session, disk) cache
                # would otherwise leave THIS session unable to resolve its
                # [REF:id] via expand_context.
                if self.shadow is not None:
                    self.shadow.record(hit.compressed, content, kind=kind)
                return hit.compressed, True

        compressed = self.strategy.compress(content, query=intent, kind=kind)

        if self.shadow is not None:
            self.shadow.record(compressed, content, kind=kind)

        # Do NOT cache a passthrough. GpuServerStrategy returns the content
        # unchanged when the hosted GPU is unreachable (e.g. a RunPod cold
        # start), so caching that would freeze an uncompressed result into the
        # benchmark and understate every arm that happened to run during the
        # outage. Only a real compression -- strictly smaller than the input --
        # is cached; a passthrough is retried on the next sweep.
        if self.cache is not None and compressed != content:
            self.cache.put(
                content, kind, intent,
                compressed=compressed,
                original_tokens=count_tokens(content),
                compressed_tokens=count_tokens(compressed),
            )
        return compressed, False

    def process(self, messages: list[dict], arm: Arm) -> RequestOutcome:
        """Apply `arm` to every compressible segment in `messages`.

        Only `role: "tool"` messages are compressed. System and user turns are
        protected content, and assistant turns are the model's own output --
        compressing those is a different feature with different risks, and
        conflating them would muddy the measurement.
        """
        outcome = RequestOutcome(arm=arm.name, messages=list(messages))
        tool_index = build_tool_call_index(messages)
        min_tokens = self.config.policy.min_tokens
        max_tokens = self.config.policy.max_tokens

        for i, msg in enumerate(messages):
            if msg.get("role") != "tool":
                continue
            content = msg.get("content") or ""
            if not isinstance(content, str):
                continue

            original_tokens = count_tokens(content)
            kind, tool_name, file_path = self.decide_kind(msg, arm, tool_index)
            intent = self.decide_intent(messages, i, arm, tool_index)

            # Passthrough arm: record the segment untouched. Used to record
            # neutral reference trajectories, where the agent must see full,
            # uncompressed context.
            if not arm.compress:
                outcome.segments.append(SegmentOutcome(
                    index=i, kind=kind, intent=intent.text,
                    intent_source=intent.source,
                    original_tokens=original_tokens,
                    compressed_tokens=original_tokens,
                    retention=1.0, compressed=content,
                    skipped="passthrough",
                    tool_name=tool_name, file_path=file_path,
                ))
                continue

            # Not worth a GPU round-trip, and small segments are where
            # compression overhead most often exceeds the saving.
            if original_tokens < min_tokens or original_tokens > max_tokens:
                outcome.segments.append(SegmentOutcome(
                    index=i, kind=kind, intent=intent.text,
                    intent_source=intent.source,
                    original_tokens=original_tokens,
                    compressed_tokens=original_tokens,
                    retention=1.0, compressed=content,
                    skipped="below_min_tokens" if original_tokens < min_tokens
                            else "above_max_tokens",
                    tool_name=tool_name, file_path=file_path,
                ))
                continue

            # Adaptive: `kind` above is the real classification (kept for
            # telemetry/bucketing regardless of what happens next). The
            # controller decides, per segment and per bucket, whether THIS
            # compression actually gets kind+intent (routed, segpilot-style)
            # or neither (conservative, stock-style) -- a single either/or
            # choice, not four independent combinations.
            send_kind, send_intent = kind, intent
            if arm.controller is not None:
                if not arm.controller.should_route(kind):
                    send_kind, send_intent = None, Intent(text="", source="adaptive_conservative")
                arm.controller.record_compression(kind)

            compressed, cache_hit = self.compress_segment(
                content, kind=send_kind, intent=send_intent.text
            )
            report = score_retention(content, compressed, kind=send_kind or "file_read")
            guard_tripped = False

            # Guard: if too much must-keep content was destroyed, keep the
            # original. Paying full price beats handing the agent a context
            # with the identifiers it needs stripped out.
            if arm.use_guard and report.retention < self.config.policy.min_retention:
                guard_tripped = True
                compressed = content
                report = score_retention(content, content, kind=send_kind or "file_read")

            outcome.segments.append(SegmentOutcome(
                index=i, kind=kind, intent=send_intent.text,
                intent_source=send_intent.source,
                original_tokens=original_tokens,
                compressed_tokens=count_tokens(compressed),
                retention=report.retention,
                compressed=compressed,
                cache_hit=cache_hit,
                guard_tripped=guard_tripped,
                tool_name=tool_name, file_path=file_path,
            ))

        return outcome

    def apply(self, messages: list[dict], arm: Arm) -> tuple[list[dict], RequestOutcome]:
        """Process, then return the rewritten messages ready to forward upstream."""
        outcome = self.process(messages, arm)
        rewritten = list(messages)
        for seg in outcome.segments:
            if seg.skipped:
                continue
            rewritten[seg.index] = {**rewritten[seg.index], "content": seg.compressed}
        return rewritten, outcome
