"""Layer 2 — the retention guard.

Compression is only safe if the facts an agent needs survive it. This module
answers one question, cheaply and without an LLM:

    Of the spans Paritok's own training pipeline considers "must keep"
    (file paths, identifiers, error classes, line numbers, hashes, URLs),
    what fraction is still present after compression?

That number is `retention`. It is the metric the guard gates on and the metric
the replay harness plots quality against. Using Paritok's own definition
(vendored in `segpilot.vendor.mustkeep`) matters: it is not a yardstick we
invented to flatter ourselves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from segpilot.vendor.mustkeep import find_must_keep_spans

# Compressed bodies arrive tagged as "[REF:abc123 src=foo.py] <body>". The tag
# is bookkeeping, not content, so it is stripped before scoring.
_REF_TAG = re.compile(r"^\[REF:[a-f0-9]+(?:\s+src=[^\]]*)?\]\s*")


def strip_ref_tag(text: str) -> str:
    return _REF_TAG.sub("", text, count=1)


def _normalize(text: str) -> str:
    """Collapse whitespace so re-wrapped output is not scored as data loss.

    Compression legitimately reflows text. A path that survives with different
    surrounding spacing has not been lost, so whitespace is normalized on both
    sides before the containment check.
    """
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class RetentionReport:
    """Outcome of scoring one compression."""

    total: int = 0
    kept: int = 0
    lost: list[dict] = field(default_factory=list)

    @property
    def retention(self) -> float:
        """Fraction of must-keep spans that survived. 1.0 when nothing was at risk."""
        if self.total == 0:
            return 1.0
        return self.kept / self.total

    @property
    def lost_kinds(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for span in self.lost:
            counts[span["kind"]] = counts.get(span["kind"], 0) + 1
        return counts

    def summary(self) -> str:
        if self.total == 0:
            return "no must-keep spans"
        return f"{self.kept}/{self.total} kept ({self.retention:.1%})"


def score_retention(
    original: str,
    compressed: str,
    *,
    kind: str = "file_read",
    seg_id: str = "s0",
) -> RetentionReport:
    """Score how much must-keep content survived a compression.

    A span counts as kept when its text appears anywhere in the compressed body.
    Position is deliberately ignored: compression reorders and summarizes, and
    demanding positional stability would punish behaviour that is working as
    intended.
    """
    spans = find_must_keep_spans(original, seg_id, kind)
    report = RetentionReport(total=len(spans))
    if not spans:
        return report

    haystack = _normalize(strip_ref_tag(compressed))
    for span in spans:
        needle = _normalize(span["text"])
        if needle and needle in haystack:
            report.kept += 1
        else:
            report.lost.append(span)
    return report
