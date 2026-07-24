"""Turn replay metrics into a Markdown report and a self-contained SVG Pareto plot.

No plotting dependency: the SVG is emitted by hand so the whole toolchain stays
`pip install -e .` with nothing else. The report leads with the honest caveat
about raw retention rather than burying it.
"""

from __future__ import annotations

from pathlib import Path

from segpilot.replay.metrics import ArmMetrics
from segpilot.replay.harness import SessionReplay, aggregate


def _fmt_pct(x: float) -> str:
    return f"{x:.0%}"


def population_of(task_id: str) -> str:
    """Which benchmark population a session belongs to.

    'campaign' sessions chain several bugs and drift; 'single-bug' sessions are
    short and do not. They are reported separately so the neutral single-bug
    result is never hidden inside a campaign average, and vice versa.
    """
    return "campaign" if task_id.startswith("campaign") else "single-bug"


def _aggregate_table(totals: dict[str, ArmMetrics], arms: list[str]) -> list[str]:
    base = totals.get("stock")
    lines = [
        "| arm | ratio | saved | must-keep ret. | task ret. | rel/1k | Δ vs stock | guard trips |",
        "|---|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for a in arms:
        m = totals.get(a)
        if m is None:
            continue
        delta = ""
        if base and a != "stock" and base.task_relevance_per_1k:
            delta = f"{m.task_relevance_per_1k / base.task_relevance_per_1k:.2f}×"
        lines.append(
            f"| `{a}` | {m.ratio:.3f} | {_fmt_pct(m.saving_pct)} | "
            f"{_fmt_pct(m.mustkeep_retention)} | {_fmt_pct(m.task_retention)} | "
            f"{m.task_relevance_per_1k:.2f} | {delta} | {m.guard_trips} |"
        )
    lines.append("")
    return lines


def markdown_report(
    totals: dict[str, ArmMetrics],
    replays: list[SessionReplay],
    arms: list[str],
    *,
    solve_rate: dict[str, tuple[int, int]] | None = None,
) -> str:
    lines: list[str] = []
    lines.append("# SEGPILOT replay report\n")
    lines.append(
        f"Offline replay of **{len(replays)} recorded session(s)** under "
        f"{len(arms)} compression arms. Compression ran on Paritok's hosted GPU; "
        "every arm was applied to identical, uncompressed reference content.\n"
    )
    lines.append(
        "> **Verdict (see [results.md](../../docs/results.md)):** intent/kind "
        "routing does not reliably beat stock. `kind_only` equals `stock` "
        "everywhere; the only mover is must-keep retention under intent, and it is "
        "small (+~9pp mean) and inconsistent (it reverses on one of three drift "
        "sessions). `task ret.` is essentially constant across arms, so `rel/1k` is "
        "flat and reflects only how hard each arm compressed. Read must-keep "
        "retention as the discriminating axis, not rel/1k.\n"
    )

    # Split by population. The thesis is about drift, which only campaigns
    # exhibit, so campaign and single-bug numbers must never be averaged
    # together -- that would let one hide the other in either direction.
    by_pop: dict[str, list[SessionReplay]] = {"campaign": [], "single-bug": []}
    for r in replays:
        by_pop[population_of(r.task_id)].append(r)

    for pop in ("campaign", "single-bug"):
        subset = by_pop[pop]
        if not subset:
            continue
        pop_totals = aggregate(subset, arms)
        lines.append(f"## Aggregate — {pop} ({len(subset)} session(s))\n")
        lines += _aggregate_table(pop_totals, arms)

    lines.append("## Aggregate — all sessions\n")
    lines += _aggregate_table(totals, arms)

    lines.append("> **must-keep ret.** = fraction of Paritok's own must-keep spans "
                 "(paths, identifiers, error classes) surviving compression — the axis "
                 "on which the arms actually differ. **rel/1k** = task-relevant tokens "
                 "retained per 1000 spent; it is flat here because task retention is "
                 "constant across arms, so it measures only compression aggressiveness. "
                 "Campaign sessions are the drift test; single-bug sessions are the "
                 "no-drift control.\n")

    if solve_rate:
        lines.append("## Live solve rate\n")
        lines.append("Task success from *live* agent runs (pytest passes), not replay. "
                     "Small sample; reported per arm.\n")
        lines.append("| arm | solved | tasks | rate |")
        lines.append("|---|--:|--:|--:|")
        for a, (solved, total) in solve_rate.items():
            rate = f"{solved / total:.0%}" if total else "—"
            lines.append(f"| `{a}` | {solved} | {total} | {rate} |")
        lines.append("")

    lines.append("## Per task\n")
    lines.append("| task | arm | ratio | task ret. | rel/1k |")
    lines.append("|---|---|--:|--:|--:|")
    for r in replays:
        for a in arms:
            m = r.per_arm.get(a)
            if not m:
                continue
            lines.append(
                f"| {r.task_id} | `{a}` | {m.ratio:.3f} | "
                f"{_fmt_pct(m.task_retention)} | {m.task_relevance_per_1k:.2f} |"
            )
    lines.append("")
    return "\n".join(lines)


def pareto_svg(totals: dict[str, ArmMetrics], arms: list[str]) -> str:
    """Scatter of spend (x = compressed tokens) vs must-keep retention (y).

    Must-keep retention -- Paritok's own training-time definition of content that
    must survive -- is the axis on which the arms actually differ; task-relevant
    retention saturates across arms, so plotting it would show a flat line and
    hide the (weak, inconsistent) effect that does exist. The frontier is
    up-and-left: keep more must-keep content, spend fewer tokens.
    """
    W, H, pad = 640, 420, 70
    xs = [totals[a].compressed_tokens for a in arms]
    ys = [totals[a].mustkeep_retention for a in arms]
    xmax = max(xs) * 1.1 if xs and max(xs) else 1.0
    ymin = min(0.0, min(ys) if ys else 0.0)

    def px(x): return pad + (x / xmax) * (W - 2 * pad)
    def py(y): return H - pad - ((y - ymin) / (1.0 - ymin + 1e-9)) * (H - 2 * pad)

    palette = {
        "stock": "#94a3b8", "kind_only": "#38bdf8", "intent_only": "#a78bfa",
        "segpilot": "#34d399", "segpilot+guard": "#fbbf24", "raw": "#64748b",
    }
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" font-family="ui-sans-serif,system-ui,sans-serif">',
        f'<rect width="{W}" height="{H}" fill="#0b1220"/>',
        # axes
        f'<line x1="{pad}" y1="{H-pad}" x2="{W-pad}" y2="{H-pad}" stroke="#334155"/>',
        f'<line x1="{pad}" y1="{pad}" x2="{pad}" y2="{H-pad}" stroke="#334155"/>',
        f'<text x="{W/2}" y="{H-24}" fill="#94a3b8" font-size="13" '
        f'text-anchor="middle">tokens spent per session (lower is cheaper →)</text>',
        f'<text x="24" y="{H/2}" fill="#94a3b8" font-size="13" '
        f'text-anchor="middle" transform="rotate(-90 24 {H/2})">must-keep retention →</text>',
        f'<text x="{pad}" y="{pad-24}" fill="#e2e8f0" font-size="15" '
        f'font-weight="600">SEGPILOT — spend vs task-relevant retention</text>',
        f'<text x="{pad}" y="{pad-6}" fill="#64748b" font-size="11">'
        f'up and to the left is better</text>',
    ]
    # y gridlines
    for frac in (0.25, 0.5, 0.75, 1.0):
        y = py(frac)
        parts.append(f'<line x1="{pad}" y1="{y:.1f}" x2="{W-pad}" y2="{y:.1f}" '
                     f'stroke="#1e293b"/>')
        parts.append(f'<text x="{pad-8}" y="{y+4:.1f}" fill="#64748b" font-size="11" '
                     f'text-anchor="end">{frac:.0%}</text>')
    # points
    for a in arms:
        m = totals[a]
        x, y = px(m.compressed_tokens), py(m.mustkeep_retention)
        color = palette.get(a, "#e2e8f0")
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="7" fill="{color}"/>')
        parts.append(f'<text x="{x+11:.1f}" y="{y+4:.1f}" fill="{color}" '
                     f'font-size="12">{a}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def write_report(
    totals: dict[str, ArmMetrics],
    replays: list[SessionReplay],
    arms: list[str],
    *,
    out_dir: str | Path = "examples/reports",
    solve_rate: dict[str, tuple[int, int]] | None = None,
) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    md_path = out / "replay_report.md"
    svg_path = out / "pareto.svg"
    md_path.write_text(
        markdown_report(totals, replays, arms, solve_rate=solve_rate) +
        f"\n![Pareto](pareto.svg)\n",
        encoding="utf-8",
    )
    svg_path.write_text(pareto_svg(totals, arms), encoding="utf-8")
    return {"markdown": md_path, "svg": svg_path}


def main(argv: list[str] | None = None) -> int:
    """Replay all reference sessions and write the report.

        python -m segpilot.replay.report --out examples/reports/

    Replay is served from the compression cache, so this is deterministic and
    makes no network calls once the cache is warm.
    """
    import argparse

    from segpilot.compressor import COMPRESSING_ARMS
    from segpilot.config import SegpilotConfig
    from segpilot.replay.harness import run
    from segpilot.replay.session import find_sessions, load_session

    ap = argparse.ArgumentParser(description="Write the SEGPILOT replay report")
    ap.add_argument("--sessions-dir", default="examples/sessions")
    ap.add_argument("--suffix", default="__raw",
                    help="session filename suffix (default: __raw references)")
    ap.add_argument("--arms", default=",".join(COMPRESSING_ARMS))
    ap.add_argument("--out", default="examples/reports")
    args = ap.parse_args(argv)

    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    paths = find_sessions(args.sessions_dir, suffix=args.suffix)
    if not paths:
        print(f"no sessions matching *{args.suffix}.jsonl in {args.sessions_dir}")
        return 1
    sessions = [load_session(p) for p in paths]

    cfg = SegpilotConfig.load()
    if cfg.paritok.require_api_key and not cfg.paritok.api_key:
        print("PARITOK_API_KEY not set; an uncached segment would need it.")
        return 1

    replays, totals = run(sessions, config=cfg, arms=arms)
    written = write_report(totals, replays, arms, out_dir=args.out)
    for kind, path in written.items():
        print(f"  wrote {kind}: {path}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
