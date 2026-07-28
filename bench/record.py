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
import json
import sys
import time
from pathlib import Path

from agent.ruff import main as run_agent
from bench.harness import list_tasks
from segpilot.compressor import ARMS
from segpilot.config import SegpilotConfig


def preflight_gpu_if_needed(arms: list[str]) -> None:
    """Refuse to record a compressing arm while the hosted GPU is unreachable.

    GpuServerStrategy degrades to passthrough when `gpu_available` is false, so a
    segpilot run recorded during an outage would silently contain no compression
    -- a session mislabelled as compressed, quietly poisoning the benchmark. The
    raw arm does not compress, so it is exempt.
    """
    needs_gpu = any(ARMS[a].compress for a in arms if a in ARMS)
    if not needs_gpu:
        return
    from paritok.strategies.gpu_server import GpuServerStrategy

    cfg = SegpilotConfig.load()
    if cfg.paritok.require_api_key and not cfg.paritok.api_key:
        raise SystemExit("PARITOK_API_KEY not set; compressing arms need it.")
    available, message = GpuServerStrategy(cfg.to_paritok_config().gpu_server).check()
    if not available:
        raise SystemExit(
            "Hosted GPU is not available -- refusing to record compressing arms, "
            "because compression would silently pass through and the sessions "
            f"would be mislabelled.\n  reason: {message}"
        )
    print(f"[preflight] hosted GPU OK: {message}")


def _session_path(sessions_dir: str, task_id: str, arm: str) -> Path:
    return Path(sessions_dir) / f"{task_id}__{arm}.jsonl"


def _session_health(path: Path) -> tuple[int, str]:
    """(tool_result_count, stop_reason) for a written session, or (0, '') if
    unreadable. Lets us tell a real run from one the rate limiter killed."""
    if not path.exists():
        return 0, ""
    tool_results, stop_reason = 0, ""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                row = json.loads(line)
                if row.get("type") == "meta":
                    stop_reason = row.get("result", {}).get("stop_reason", "")
                elif row.get("type") == "messages":
                    tool_results = sum(
                        1 for m in row.get("messages", []) if m.get("role") == "tool"
                    )
    except (OSError, ValueError):
        return 0, ""
    return tool_results, stop_reason


def record_one(task_id: str, arm: str, sessions_dir: str, *, force: bool,
               max_turns: int, live: bool = False) -> str:
    """Returns 'skipped' | 'solved' | 'unsolved' | 'ratelimited' | 'empty'.

    A session that the rate limiter killed is deleted rather than kept, so a
    resumed run re-records it instead of skipping a broken file. What counts
    as "killed" differs by purpose:

      reference (raw) sessions are only useful for their accumulated content,
      not for a task outcome, so a partial-but-rich transcript (real turns, real
      tool results) is still valid even if the run was later cut off -- only a
      genuinely empty one (0 tool results) gets discarded.

      live sessions (`live=True`) exist to measure solve rate, so ANY run that
      ended in upstream_error is invalid regardless of how much partial content
      it accumulated: "unsolved" only means something if the agent actually got
      to finish. We saw this concretely recording campaign_1's live A/B --
      `stock` reached 5 real turns before a 429, and without this check it would
      have been recorded as a legitimate "unsolved," when really it just ran out
      of quota mid-task.
    """
    path = _session_path(sessions_dir, task_id, arm)
    if path.exists() and not force:
        tool_results, stop_reason = _session_health(path)
        was_interrupted = "upstream_error" in stop_reason
        if tool_results > 0 and not (live and was_interrupted):
            return "skipped"          # genuinely done
        path.unlink(missing_ok=True)  # empty or invalidated leftover -> re-record

    try:
        rc = run_agent([
            "--task", task_id, "--arm", arm,
            "--sessions-dir", sessions_dir,
            "--max-turns", str(max_turns), "--quiet",
        ])
    except Exception as exc:  # noqa: BLE001 — one bad task must not sink the batch
        print(f"    ! {task_id}/{arm}: {type(exc).__name__}: {exc}")
        return "empty"

    tool_results, stop_reason = _session_health(path)
    was_interrupted = "upstream_error" in stop_reason
    if was_interrupted and (tool_results == 0 or live):
        # Quota/rate wall: the run never really started (reference), or started
        # but never reached a real outcome (live). Drop the file so it
        # re-records on the next run, and let the caller stop the batch.
        path.unlink(missing_ok=True)
        return "ratelimited"
    if tool_results == 0:
        path.unlink(missing_ok=True)
        return "empty"
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

    # Fail loud before spending any quota if a compressing arm is requested but
    # the GPU cannot compress. Raw-only reference runs skip this.
    preflight_gpu_if_needed([arm for _, arm in plan])

    print(f"recording {len(plan)} session(s): {len(tasks)} task(s)")
    counts = {"skipped": 0, "solved": 0, "unsolved": 0, "ratelimited": 0, "empty": 0}
    hit_wall = False
    for i, (task_id, arm) in enumerate(plan, 1):
        print(f"\n[{i}/{len(plan)}] {task_id} / {arm}")
        status = record_one(task_id, arm, args.sessions_dir,
                            force=args.force, max_turns=args.max_turns,
                            live=(arm != "raw"))
        counts[status] += 1
        print(f"    -> {status}")
        if status == "ratelimited":
            # The quota is spent; further runs would just burn time on retries.
            # Everything recorded so far is kept; the rest re-records on resume.
            hit_wall = True
            print("\n  Rate limit hit. Stopping so we don't hammer a spent quota.")
            print("  Re-run the same command later to resume the remaining tasks.")
            break
        if status != "skipped" and i < len(plan):
            time.sleep(args.pause)   # spread calls out for the free tier

    print(f"\n{'=' * 50}")
    for k, v in counts.items():
        print(f"  {k:<11} {v}")
    solved = counts["solved"]
    ran = counts["solved"] + counts["unsolved"]
    if ran:
        print(f"  solve rate {solved}/{ran} = {solved / ran:.0%}")
    done = counts["skipped"] + counts["solved"] + counts["unsolved"]
    print(f"  {done}/{len(plan)} sessions complete on disk")
    return 2 if hit_wall else 0


if __name__ == "__main__":
    sys.exit(main())
