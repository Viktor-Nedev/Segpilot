"""Turn replay metrics into a Markdown report and a self-contained SVG Pareto plot.

No plotting dependency: the SVG is emitted by hand so the whole toolchain stays
`pip install -e .` with nothing else. The report leads with the honest caveat
about raw retention rather than burying it.
"""

from __future__ import annotations

from pathlib import Path

from segpilot.replay.metrics import ArmMetrics
from segpilot.replay.harness import SessionReplay


def _fmt_pct(x: float) -> str:
    return f"{x:.0%}"


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

    base = totals.get("stock")

    lines.append("## Aggregate\n")
    lines.append("| arm | ratio | saved | must-keep ret. | task ret. | rel/1k | Δ vs stock | guard trips |")
    lines.append("|---|--:|--:|--:|--:|--:|--:|--:|")
    for a in arms:
        m = totals[a]
        delta = ""
        if base and a != "stock" and base.task_relevance_per_1k:
            delta = f"{m.task_relevance_per_1k / base.task_relevance_per_1k:.2f}×"
        lines.append(
            f"| `{a}` | {m.ratio:.3f} | {_fmt_pct(m.saving_pct)} | "
            f"{_fmt_pct(m.mustkeep_retention)} | {_fmt_pct(m.task_retention)} | "
            f"{m.task_relevance_per_1k:.2f} | {delta} | {m.guard_trips} |"
        )
    lines.append("")

    lines.append("> **rel/1k** = task-relevant tokens retained per 1000 tokens spent — "
                 "the headline. Raw retention flatters whichever arm compressed least, "
                 "so it is normalised by tokens actually spent. An arm only wins by "
                 "keeping more of what the task needs for less.\n")

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
    """Scatter of spend (x = compressed tokens) vs task retention (y).

    The frontier is up-and-left: keep more of the task, spend fewer tokens.
    """
    W, H, pad = 640, 420, 70
    xs = [totals[a].compressed_tokens for a in arms]
    ys = [totals[a].task_retention for a in arms]
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
        f'text-anchor="middle" transform="rotate(-90 24 {H/2})">task-relevant retention →</text>',
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
        x, y = px(m.compressed_tokens), py(m.task_retention)
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
