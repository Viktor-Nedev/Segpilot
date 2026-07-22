"""Metrics for comparing arms. Designed to be hard to fool.

The central honesty problem: raw retention is not comparable across arms that
compress by different amounts. An arm that barely compresses trivially retains
almost everything. So the headline metric is not retention, it is

    task-relevant retention PER TOKEN spent

-- how much of the code the task actually needs survives, normalised by what you
paid to keep it. An arm only wins if it keeps more of what matters for less.

`must_appear` spans come from each task's ground truth, chosen before any run,
so "task-relevant" is not something we tuned after seeing results.
"""

from __future__ import annotations

from dataclasses import dataclass

from segpilot.compressor import RequestOutcome
from segpilot.policy.guard import _normalize, score_retention, strip_ref_tag


def task_relevant_retention(
    compressed_segments: list[str], must_appear: list[str]
) -> tuple[int, int]:
    """How many of the task's must-appear tokens survive anywhere in the
    compressed output. Returns (kept, total).

    Matching is whitespace-normalised and case-sensitive: identifiers and paths
    are case-sensitive, and compression legitimately reflows whitespace.
    """
    if not must_appear:
        return 0, 0
    haystack = _normalize(" ".join(strip_ref_tag(s) for s in compressed_segments))
    kept = sum(1 for needle in must_appear if _normalize(needle) in haystack)
    return kept, len(must_appear)


@dataclass
class ArmMetrics:
    """Aggregate numbers for one arm over one session (or many)."""

    arm: str
    original_tokens: int = 0
    compressed_tokens: int = 0
    # must-keep = Paritok's own training-time span definition (their yardstick)
    mustkeep_kept: int = 0
    mustkeep_total: int = 0
    # task-relevant = this task's ground-truth must_appear tokens (the honest one)
    task_kept: int = 0
    task_total: int = 0
    segments_compressed: int = 0
    segments_skipped: int = 0
    guard_trips: int = 0
    sessions: int = 0

    @property
    def saved_tokens(self) -> int:
        return self.original_tokens - self.compressed_tokens

    @property
    def ratio(self) -> float:
        """Compressed / original. Lower is more aggressive."""
        return self.compressed_tokens / self.original_tokens if self.original_tokens else 1.0

    @property
    def saving_pct(self) -> float:
        return 1.0 - self.ratio

    @property
    def mustkeep_retention(self) -> float:
        return self.mustkeep_kept / self.mustkeep_total if self.mustkeep_total else 1.0

    @property
    def task_retention(self) -> float:
        return self.task_kept / self.task_total if self.task_total else 1.0

    @property
    def task_relevance_per_1k(self) -> float:
        """Task-relevant tokens retained per 1000 tokens spent. THE headline.

        Combines both axes: an arm that keeps the right code (high task
        retention) cheaply (low compressed_tokens) scores high. Barely
        compressing does not help here, because the denominator grows with it.
        """
        if not self.compressed_tokens:
            return 0.0
        return 1000.0 * self.task_retention / self.compressed_tokens

    def merge(self, other: "ArmMetrics") -> None:
        assert other.arm == self.arm
        self.original_tokens += other.original_tokens
        self.compressed_tokens += other.compressed_tokens
        self.mustkeep_kept += other.mustkeep_kept
        self.mustkeep_total += other.mustkeep_total
        self.task_kept += other.task_kept
        self.task_total += other.task_total
        self.segments_compressed += other.segments_compressed
        self.segments_skipped += other.segments_skipped
        self.guard_trips += other.guard_trips
        self.sessions += other.sessions


def metrics_for_outcome(
    arm: str, outcome: RequestOutcome, must_appear: list[str]
) -> ArmMetrics:
    """Compute metrics for one arm applied to one session's messages."""
    m = ArmMetrics(arm=arm, sessions=1)
    m.original_tokens = outcome.original_tokens
    m.compressed_tokens = outcome.compressed_tokens

    for seg in outcome.segments:
        if seg.skipped and seg.skipped != "passthrough":
            m.segments_skipped += 1
        elif not seg.skipped:
            m.segments_compressed += 1
        if seg.guard_tripped:
            m.guard_trips += 1
        # Must-keep retention, weighted by span count so a big file is not
        # outvoted by a small one. Recompute counts (not the per-segment float)
        # from the original content, recoverable via the segment's index.
        original = outcome.messages[seg.index].get("content") or ""
        if isinstance(original, str) and original:
            report = score_retention(original, seg.compressed, kind=seg.kind or "file_read")
            m.mustkeep_kept += report.kept
            m.mustkeep_total += report.total

    # Task-relevant retention is computed over the whole compressed request: the
    # needed code may survive in any segment, not necessarily its original one.
    compressed_bodies = [
        seg.compressed for seg in outcome.segments if seg.compressed
    ]
    kept, total = task_relevant_retention(compressed_bodies, must_appear)
    m.task_kept, m.task_total = kept, total
    return m
