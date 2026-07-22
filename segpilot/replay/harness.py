"""Replay a recorded session under every arm and collect metrics.

    python -m segpilot.replay.harness --all
    python -m segpilot.replay.harness --session examples/sessions/bug_01__raw.jsonl

This is offline: it applies `SegpilotCompressor` to the recorded (uncompressed)
messages under each arm. The first sweep hits the hosted GPU to compress each
distinct (content, kind, intent); every later sweep is served from the cache, so
re-running is free and the numbers are stable between runs.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field

from segpilot.compressor import ARMS, COMPRESSING_ARMS, SegpilotCompressor
from segpilot.config import SegpilotConfig
from segpilot.policy.cache import CompressionCache
from segpilot.replay.metrics import ArmMetrics, metrics_for_outcome
from segpilot.replay.session import Session, find_sessions, load_session


@dataclass
class SessionReplay:
    """Every arm's metrics for one session."""

    task_id: str
    recorded_arm: str
    per_arm: dict[str, ArmMetrics] = field(default_factory=dict)


def replay_session(
    session: Session, compressor: SegpilotCompressor, arms: list[str]
) -> SessionReplay:
    result = SessionReplay(task_id=session.task_id, recorded_arm=session.recorded_arm)
    for arm_name in arms:
        outcome = compressor.process(session.messages, ARMS[arm_name])
        result.per_arm[arm_name] = metrics_for_outcome(
            arm_name, outcome, session.must_appear
        )
    return result


def aggregate(replays: list[SessionReplay], arms: list[str]) -> dict[str, ArmMetrics]:
    """Sum each arm's per-session metrics into one total per arm."""
    totals = {a: ArmMetrics(arm=a) for a in arms}
    for replay in replays:
        for arm_name in arms:
            if arm_name in replay.per_arm:
                totals[arm_name].merge(replay.per_arm[arm_name])
    return totals


def run(
    sessions: list[Session],
    *,
    config: SegpilotConfig | None = None,
    arms: list[str] | None = None,
    cache_path: str = "segpilot_cache.db",
) -> tuple[list[SessionReplay], dict[str, ArmMetrics]]:
    config = config or SegpilotConfig.load()
    arms = arms or list(COMPRESSING_ARMS)

    cache = CompressionCache(cache_path)
    try:
        compressor = SegpilotCompressor(config, cache=cache)
        replays = [replay_session(s, compressor, arms) for s in sessions]
    finally:
        cache.close()
    return replays, aggregate(replays, arms)


def _print_table(totals: dict[str, ArmMetrics], arms: list[str]) -> None:
    print(f"\n{'arm':<16} {'ratio':>7} {'saved%':>7} {'mustkeep':>9} "
          f"{'task-ret':>9} {'rel/1k':>7} {'guard':>6}")
    print("-" * 70)
    base = totals.get("stock")
    for arm_name in arms:
        m = totals[arm_name]
        star = ""
        if base and arm_name != "stock" and base.task_relevance_per_1k:
            factor = m.task_relevance_per_1k / base.task_relevance_per_1k
            star = f"  ({factor:.2f}x)"
        print(f"{arm_name:<16} {m.ratio:>7.3f} {m.saving_pct:>6.1%} "
              f"{m.mustkeep_retention:>8.0%} {m.task_retention:>8.0%} "
              f"{m.task_relevance_per_1k:>7.2f} {m.guard_trips:>6}{star}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Replay recorded sessions under each arm")
    ap.add_argument("--session", help="a single session JSONL")
    ap.add_argument("--sessions-dir", default="examples/sessions")
    ap.add_argument("--suffix", default="__raw",
                    help="session filename suffix to load (default: __raw references)")
    ap.add_argument("--all", action="store_true", help="every session in --sessions-dir")
    ap.add_argument("--arms", default=",".join(COMPRESSING_ARMS))
    args = ap.parse_args(argv)

    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    for a in arms:
        if a not in ARMS:
            print(f"unknown arm: {a}. Known: {sorted(ARMS)}")
            return 1

    if args.session:
        paths = [args.session]
    elif args.all:
        paths = find_sessions(args.sessions_dir, suffix=args.suffix)
        if not paths:
            print(f"no sessions matching *{args.suffix}.jsonl in {args.sessions_dir}")
            return 1
    else:
        print("pass --session <file> or --all")
        return 1

    sessions = [load_session(p) for p in paths]
    print(f"replaying {len(sessions)} session(s) under arms: {', '.join(arms)}")

    cfg = SegpilotConfig.load()
    if cfg.paritok.require_api_key and not cfg.paritok.api_key:
        print("PARITOK_API_KEY not set; the first (uncached) sweep needs it.")
        return 1

    replays, totals = run(sessions, config=cfg, arms=arms)

    # Per-task, so a single easy or hard task cannot hide behind the average.
    for replay in replays:
        print(f"\n=== {replay.task_id} (recorded under {replay.recorded_arm}) ===")
        _print_table(replay.per_arm, arms)

    print(f"\n{'=' * 70}\nAGGREGATE across {len(sessions)} sessions\n{'=' * 70}")
    _print_table(totals, arms)

    print("\nrel/1k = task-relevant tokens retained per 1000 tokens spent (higher is")
    print("better). This is the honest headline: raw retention flatters whichever")
    print("arm compressed least, so we normalise by tokens actually spent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
