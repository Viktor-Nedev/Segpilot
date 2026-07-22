"""Record agent sessions for the benchmark, pacing for Gemini's free tier.

    python -m bench.record --references            # raw reference per task
    python -m bench.record --live --arms stock,segpilot

Two kinds of recording:

  references  -- run each task under the `raw` arm (no compression). These are
                 the neutral trajectories the offline replay sweeps over.
  live        -- run each task under the named arms for real, to measure solve
                 rate. This is the quota-expensive part.

Both are resumable: a session file that already exists is skipped unless
--force, so a run interrupted by a rate limit can simply be re-invoked.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from agent.ruff import main as run_agent
from bench.harness import list_tasks


def _session_path(sessions_dir: str, task_id: str, arm: str) -> Path:
    return Path(sessions_dir) / f"{task_id}__{arm}.jsonl"


def record_one(task_id: str, arm: str, sessions_dir: str, *, force: bool,
               max_turns: int) -> str:
    """Returns 'skipped' | 'solved' | 'unsolved' | 'error'."""
    path = _session_path(sessions_dir, task_id, arm)
    if path.exists() and not force:
        return "skipped"
    try:
        rc = run_agent([
            "--task", task_id, "--arm", arm,
            "--sessions-dir", sessions_dir,
            "--max-turns", str(max_turns), "--quiet",
        ])
    except Exception as exc:  # noqa: BLE001 — one bad task must not sink the batch
        print(f"    ! {task_id}/{arm}: {type(exc).__name__}: {exc}")
        return "error"
    return "solved" if rc == 0 else "unsolved"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Record benchmark sessions")
    ap.add_argument("--references", action="store_true",
                    help="record neutral raw-arm reference trajectories")
    ap.add_argument("--live", action="store_true",
                    help="record live runs under --arms for solve rate")
    ap.add_argument("--arms", default="stock,segpilot",
                    help="arms for --live (default: stock,segpilot)")
    ap.add_argument("--tasks", default="", help="comma list; default all")
    ap.add_argument("--sessions-dir", default="examples/sessions")
    ap.add_argument("--max-turns", type=int, default=25)
    ap.add_argument("--pause", type=float, default=8.0,
                    help="seconds to wait between runs, to respect rate limits")
    ap.add_argument("--force", action="store_true", help="re-record existing sessions")
    args = ap.parse_args(argv)

    if not (args.references or args.live):
        print("pass --references and/or --live")
        return 1

    tasks = [t.strip() for t in args.tasks.split(",") if t.strip()] or list_tasks()
    plan: list[tuple[str, str]] = []
    if args.references:
        plan += [(t, "raw") for t in tasks]
    if args.live:
        for a in (x.strip() for x in args.arms.split(",") if x.strip()):
            plan += [(t, a) for t in tasks]

    print(f"recording {len(plan)} session(s): {len(tasks)} task(s)")
    counts = {"skipped": 0, "solved": 0, "unsolved": 0, "error": 0}
    for i, (task_id, arm) in enumerate(plan, 1):
        print(f"\n[{i}/{len(plan)}] {task_id} / {arm}")
        status = record_one(task_id, arm, args.sessions_dir,
                            force=args.force, max_turns=args.max_turns)
        counts[status] += 1
        print(f"    -> {status}")
        if status != "skipped" and i < len(plan):
            time.sleep(args.pause)   # spread calls out for the free tier

    print(f"\n{'=' * 50}")
    for k, v in counts.items():
        print(f"  {k:<10} {v}")
    solved = counts["solved"]
    ran = counts["solved"] + counts["unsolved"]
    if ran:
        print(f"  solve rate {solved}/{ran} = {solved / ran:.0%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
