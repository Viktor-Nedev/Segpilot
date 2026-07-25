"""The adaptive regret loop: per-(kind) bucket, learn whether routing helps.

WHY THIS EXISTS
---------------
Plain per-segment routing (the `segpilot` arm) does not reliably beat stock
(see docs/results.md): kind routing does nothing, intent routing is weak and
inconsistent -- it reverses on some drift sessions. Blindly routing every
segment is not defensible on that evidence.

`expand_context` is a free supervision signal Paritok's own middleware
generates but nothing reads: every time the model asks for content back, the
compression that dropped it was too aggressive for that segment. This
controller watches that signal, per `kind` bucket, and only routes
(kind+intent) where the evidence says it is not backfiring. Everywhere else it
stays conservative -- compresses the way stock does (no kind, no intent).

SCOPE: ONLINE, WITHIN-SESSION
------------------------------
Regret is a property of a live model's behaviour -- it cannot be replayed
offline against a fixed transcript, because a different compression would have
led the agent down a different path. So this controller learns within a single
live session and resets with it; there is no cross-session persistence. That
also means "adaptive" is a live-only arm (see agent/ruff.py), not one the
offline replay harness can sweep.

THRESHOLDS ARE FIXED BEFORE ANY MEASUREMENT
--------------------------------------------
MIN_OBSERVATIONS / ESCALATE_REGRET_MAX / BACKOFF_REGRET_MIN are set once, here,
before any live run under this controller. Changing them after seeing results
would be exactly the kind of post-hoc tuning this project has been built to
avoid (see the two retracted numbers in docs/results.md). If they turn out
wrong, that is a finding to report, not a knob to quietly adjust.
"""

from __future__ import annotations

from dataclasses import dataclass, field

CONSERVATIVE = "conservative"   # compress like stock: no kind, no intent
ROUTED = "routed"               # compress like segpilot: kind + intent


@dataclass
class BucketState:
    kind: str
    level: str = CONSERVATIVE
    observations: int = 0
    regrets: int = 0
    transitions: list[str] = field(default_factory=list)   # audit trail

    @property
    def regret_rate(self) -> float:
        return self.regrets / self.observations if self.observations else 0.0


class AdaptiveController:
    """One controller per live session. Buckets by `kind`."""

    # Fixed a priori (see module docstring). Do not tune these to a result.
    MIN_OBSERVATIONS = 5
    ESCALATE_REGRET_MAX = 0.05
    BACKOFF_REGRET_MIN = 0.20

    def __init__(self):
        self._buckets: dict[str, BucketState] = {}

    def _bucket(self, kind: str | None) -> BucketState:
        key = kind or "unknown"
        return self._buckets.setdefault(key, BucketState(kind=key))

    def should_route(self, kind: str | None) -> bool:
        """True -> compress this segment with kind+intent (segpilot-style).
        False -> compress conservatively (stock-style: no kind, no intent)."""
        return self._bucket(kind).level == ROUTED

    def record_compression(self, kind: str | None) -> None:
        """Call once per segment actually compressed, regardless of outcome --
        this is the denominator for the bucket's regret rate."""
        b = self._bucket(kind)
        b.observations += 1
        self._maybe_transition(b)

    def record_regret(self, kind: str | None) -> None:
        """Call once per `expand_context` resolved for a segment of this kind."""
        b = self._bucket(kind)
        b.regrets += 1
        self._maybe_transition(b)

    def _maybe_transition(self, b: BucketState) -> None:
        if b.observations < self.MIN_OBSERVATIONS:
            return
        rate = b.regret_rate
        if b.level == CONSERVATIVE and rate <= self.ESCALATE_REGRET_MAX:
            b.level = ROUTED
            b.transitions.append(
                f"obs={b.observations} regret={rate:.2f} -> escalate to routed"
            )
        elif b.level == ROUTED and rate >= self.BACKOFF_REGRET_MIN:
            b.level = CONSERVATIVE
            b.transitions.append(
                f"obs={b.observations} regret={rate:.2f} -> back off to conservative"
            )

    def snapshot(self) -> dict:
        """A serialisable view, for telemetry and the session record."""
        return {
            k: {
                "level": b.level,
                "observations": b.observations,
                "regrets": b.regrets,
                "regret_rate": round(b.regret_rate, 3),
                "transitions": list(b.transitions),
            }
            for k, b in self._buckets.items()
        }
