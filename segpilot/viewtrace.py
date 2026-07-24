"""View a Paritok compression trace: original vs compressed, per segment.

    python -m segpilot.viewtrace compress_trace.jsonl
    python -m segpilot.viewtrace compress_trace.jsonl --full
    python -m segpilot.viewtrace compress_trace.jsonl --markdown > trace.md

WHY THIS EXISTS
---------------
Paritok's own code points users at a trace viewer that is not in the repository.
`paritok/config.py` (TraceConfig) says *"Inspect it with `python
tools/view_trace.py`"* and `paritok/pipelines/compress.py` repeats *"View:
tools/view_trace.py"*, but there is no `tools/` directory and no `view_trace`
module in the PyPI package (finding #7 in docs/findings.md). The trace format is
genuinely useful for auditing what compression kept and dropped, so the missing
viewer is a real gap.

This is that viewer. It reads the exact JSONL the pipeline writes -- one record
per compression with `original`, `compressed`, token counts and ratio, plus skip
records -- and renders a readable before/after. We are happy to contribute it
upstream.

TRACE FORMAT (from paritok/pipelines/compress.py `_debug_dump`)
---------------------------------------------------------------
Compression record:
    {"ts", "elapsed_s", "query", "original_tokens", "compressed_tokens",
     "ratio", "shadow_id", "original", "compressed"}
Skip record:
    {"ts", "skipped": true, "reason", "original_tokens"}
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TraceStats:
    events: int = 0
    skipped: int = 0
    original_tokens: int = 0
    compressed_tokens: int = 0

    @property
    def ratio(self) -> float:
        return self.compressed_tokens / self.original_tokens if self.original_tokens else 1.0

    @property
    def saved(self) -> int:
        return self.original_tokens - self.compressed_tokens


def _truncate(text: str, limit: int) -> str:
    text = text or ""
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"\n  … [{len(text) - limit} more chars]"


def _bar(ratio: float, width: int = 24) -> str:
    """A tiny text meter of how much survived (filled = kept)."""
    filled = max(0, min(width, round(ratio * width)))
    return "█" * filled + "·" * (width - filled)


def load_trace(path: str | Path) -> list[dict]:
    records = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(f"  warning: skipping malformed line {n}: {exc}", file=sys.stderr)
    return records


def render_text(records: list[dict], *, body_chars: int = 600) -> str:
    out: list[str] = []
    stats = TraceStats()
    for i, rec in enumerate(records, 1):
        if rec.get("skipped"):
            stats.skipped += 1
            ot = rec.get("original_tokens", 0)
            out.append(f"[{i:>3}] SKIPPED ({rec.get('reason', '?')})  {ot} tok")
            continue
        stats.events += 1
        ot = rec.get("original_tokens", 0)
        ct = rec.get("compressed_tokens", 0)
        stats.original_tokens += ot
        stats.compressed_tokens += ct
        ratio = ct / ot if ot else 1.0
        out.append("─" * 74)
        out.append(f"[{i:>3}] {ot:>5} → {ct:<5} tok   ratio {ratio:5.1%}  {_bar(ratio)}")
        if q := rec.get("query"):
            out.append(f"      intent: {q}")
        if sid := rec.get("shadow_id"):
            out.append(f"      ref: {sid}")
        out.append("      ── original ──")
        out.append("      " + _truncate(rec.get("original", ""), body_chars).replace("\n", "\n      "))
        out.append("      ── compressed ──")
        out.append("      " + _truncate(rec.get("compressed", ""), body_chars).replace("\n", "\n      "))

    out.append("═" * 74)
    out.append(
        f"{stats.events} compressed, {stats.skipped} skipped | "
        f"{stats.original_tokens} → {stats.compressed_tokens} tok "
        f"(saved {stats.saved}, overall ratio {stats.ratio:.1%})"
    )
    return "\n".join(out)


def render_markdown(records: list[dict], *, body_chars: int = 800) -> str:
    out: list[str] = ["# Compression trace\n"]
    stats = TraceStats()
    for i, rec in enumerate(records, 1):
        if rec.get("skipped"):
            stats.skipped += 1
            out.append(f"### {i}. skipped — {rec.get('reason', '?')} "
                       f"({rec.get('original_tokens', 0)} tok)\n")
            continue
        stats.events += 1
        ot, ct = rec.get("original_tokens", 0), rec.get("compressed_tokens", 0)
        stats.original_tokens += ot
        stats.compressed_tokens += ct
        ratio = ct / ot if ot else 1.0
        out.append(f"### {i}. {ot} → {ct} tokens (ratio {ratio:.0%})\n")
        if q := rec.get("query"):
            out.append(f"**intent:** {q}\n")
        out.append("**original**\n")
        out.append("```\n" + _truncate(rec.get("original", ""), body_chars) + "\n```\n")
        out.append("**compressed**\n")
        out.append("```\n" + _truncate(rec.get("compressed", ""), body_chars) + "\n```\n")
    out.append(
        f"\n---\n\n**{stats.events} compressed, {stats.skipped} skipped** — "
        f"{stats.original_tokens} → {stats.compressed_tokens} tokens "
        f"(saved {stats.saved}, overall ratio {stats.ratio:.0%})\n"
    )
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="View a Paritok compression trace (compress_trace.jsonl)")
    ap.add_argument("trace", help="path to compress_trace.jsonl")
    ap.add_argument("--full", action="store_true", help="show full bodies, not truncated")
    ap.add_argument("--markdown", action="store_true", help="emit Markdown")
    ap.add_argument("--body-chars", type=int, default=600,
                    help="max characters of each body to show (ignored with --full)")
    args = ap.parse_args(argv)

    path = Path(args.trace)
    if not path.exists():
        print(f"no such trace file: {path}", file=sys.stderr)
        return 1
    records = load_trace(path)
    if not records:
        print("trace is empty", file=sys.stderr)
        return 1

    body = 10**9 if args.full else args.body_chars
    if args.markdown:
        print(render_markdown(records, body_chars=body))
    else:
        print(render_text(records, body_chars=body))
    return 0


if __name__ == "__main__":
    sys.exit(main())
