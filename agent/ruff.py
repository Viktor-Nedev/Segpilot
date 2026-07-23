"""Ruff — a small but real coding agent, run through SEGPILOT.

    python -m agent.ruff --task bug_01 --arm segpilot

The agent works on a sandboxed copy of `bench/project/` with one bug seeded in.
Before every LLM call its message history is compressed by the chosen arm, so
compression sits in the live path rather than being simulated afterwards.

Every turn is recorded to JSONL. That recording is the asset: live runs cost
Gemini quota, replay does not, so we run once and re-analyse many times.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from bench.harness import Task, list_tasks, load_task, prepare_workdir, run_tests
from segpilot.compressor import ARMS, Arm, SegpilotCompressor
from segpilot.config import SegpilotConfig
from segpilot.policy.cache import CompressionCache
from segpilot.upstream.gemini import GeminiUpstream, UpstreamError

from .tools import TOOL_SCHEMAS, execute

SYSTEM_PROMPT = """You are a precise software engineer working in a Python repository.

Your job is to find and fix a bug so that the test suite passes.

How to work:
- Before each tool call, state in one or two sentences what you are trying to
  find out and why. Think out loud.
- Start by running the tests to see the failure.
- Read the relevant source files before editing. The fix is often not in the
  file the test names -- check the modules it depends on.
- Make the smallest correct change. Do not modify tests.
- After editing, run the tests again to confirm.

When the tests pass, say DONE and stop."""

# Note on the "think out loud" instruction: narrating intent before acting is
# standard behaviour for coding agents (Claude Code, Cursor, OpenHands all do
# it), and it is what SEGPILOT's intent engine reads. It is applied identically
# to every arm -- the stock arm receives the same reasoning and simply ignores
# it in favour of `_extract_query` -- so it does not tilt the comparison. The
# first session we recorded without it produced mostly `tool_call_only` intents,
# which understates what a realistic agent gives the compressor to work with.


@dataclass
class TurnRecord:
    """One LLM round-trip, with what compression did to it."""

    turn: int
    intents: list[dict] = field(default_factory=list)
    original_tokens: int = 0
    compressed_tokens: int = 0
    prompt_tokens_billed: int = 0
    completion_tokens: int = 0
    tool_calls: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0


@dataclass
class AgentResult:
    task_id: str
    arm: str
    solved: bool
    turns: int
    original_tokens: int
    compressed_tokens: int
    billed_prompt_tokens: int
    completion_tokens: int
    wall_s: float
    stop_reason: str
    session_path: str

    @property
    def saved_tokens(self) -> int:
        return self.original_tokens - self.compressed_tokens

    @property
    def ratio(self) -> float:
        return self.compressed_tokens / self.original_tokens if self.original_tokens else 1.0


class RuffAgent:
    def __init__(
        self,
        *,
        workdir: Path,
        task: Task,
        config: SegpilotConfig,
        arm: Arm,
        compressor: SegpilotCompressor,
        upstream: GeminiUpstream,
        session_path: Path,
        verbose: bool = True,
    ):
        self.workdir = workdir
        self.task = task
        self.config = config
        self.arm = arm
        self.compressor = compressor
        self.upstream = upstream
        self.session_path = session_path
        self.verbose = verbose
        self.messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": task.instruction},
        ]
        self.turns: list[TurnRecord] = []

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg, flush=True)

    def run(self, *, max_turns: int = 25) -> AgentResult:
        t0 = time.time()
        stop_reason = "max_turns"

        for turn in range(1, max_turns + 1):
            # Compression happens here, on the real history, before the call.
            compressed_messages, outcome = self.compressor.apply(self.messages, self.arm)

            record = TurnRecord(
                turn=turn,
                original_tokens=outcome.original_tokens,
                compressed_tokens=outcome.compressed_tokens,
                intents=[
                    {"kind": s.kind, "intent": s.intent, "source": s.intent_source,
                     "orig": s.original_tokens, "comp": s.compressed_tokens,
                     "retention": round(s.retention, 3), "guard": s.guard_tripped}
                    for s in outcome.segments if not s.skipped
                ],
            )

            try:
                result = self.upstream.complete(compressed_messages, tools=TOOL_SCHEMAS)
            except UpstreamError as exc:
                stop_reason = f"upstream_error: {exc}"
                self._log(f"  ! {stop_reason}")
                break

            record.prompt_tokens_billed = result.prompt_tokens
            record.completion_tokens = result.completion_tokens
            record.elapsed_s = result.elapsed_s

            saved = outcome.saved_tokens
            self._log(
                f"  turn {turn:>2}  compressed {outcome.original_tokens:>6} -> "
                f"{outcome.compressed_tokens:<6} (saved {saved:>6})  "
                f"billed {result.prompt_tokens:>6}"
            )

            # Keep the UNCOMPRESSED message in our history. The agent's own
            # record stays faithful; compression is applied fresh each turn.
            # Otherwise losses would compound turn over turn and we would be
            # measuring repeated compression, not compression.
            self.messages.append(result.message)

            if not result.tool_calls:
                self.turns.append(record)
                if "DONE" in (result.content or "").upper():
                    stop_reason = "agent_done"
                else:
                    stop_reason = "no_tool_calls"
                self._log(f"  {result.content[:200]}")
                break

            for call in result.tool_calls:
                fn = call.get("function") or {}
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except (ValueError, TypeError):
                    args = {}
                output, ok = execute(self.workdir, name, args)
                record.tool_calls.append(name)
                self._log(f"        {name}({', '.join(f'{k}={v!r}'[:40] for k, v in args.items())}) "
                          f"-> {len(output)} chars{'' if ok else ' [error]'}")
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "content": output,
                })

            self.turns.append(record)

        solved, test_output = run_tests(self.workdir, self.task)
        wall = time.time() - t0

        result = AgentResult(
            task_id=self.task.id,
            arm=self.arm.name,
            solved=solved,
            turns=len(self.turns),
            original_tokens=sum(t.original_tokens for t in self.turns),
            compressed_tokens=sum(t.compressed_tokens for t in self.turns),
            billed_prompt_tokens=sum(t.prompt_tokens_billed for t in self.turns),
            completion_tokens=sum(t.completion_tokens for t in self.turns),
            wall_s=round(wall, 1),
            stop_reason=stop_reason,
            session_path=str(self.session_path),
        )
        self._write_session(result, test_output)
        return result

    def _write_session(self, result: AgentResult, test_output: str) -> None:
        """Persist the full session so it can be replayed offline forever."""
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.session_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "type": "meta",
                "task_id": self.task.id,
                "arm": self.arm.name,
                "instruction": self.task.instruction,
                "ground_truth": self.task.ground_truth,
                "result": asdict(result),
                "test_output_tail": test_output[-2000:],
            }, ensure_ascii=False) + "\n")
            # The uncompressed message history is what replay needs: it lets any
            # arm be applied after the fact to exactly the same content.
            f.write(json.dumps({"type": "messages", "messages": self.messages},
                               ensure_ascii=False) + "\n")
            for turn in self.turns:
                f.write(json.dumps({"type": "turn", **asdict(turn)},
                                   ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the Ruff agent on a seeded bug")
    ap.add_argument("--task", default="bug_01", help=f"one of: {list_tasks()}")
    ap.add_argument("--arm", default="segpilot", choices=sorted(ARMS))
    ap.add_argument("--max-turns", type=int, default=25)
    ap.add_argument("--sessions-dir", default="examples/sessions")
    ap.add_argument("--keep-workdir", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    cfg = SegpilotConfig.load()
    if cfg.paritok.require_api_key and not cfg.paritok.api_key:
        print("PARITOK_API_KEY is not set. Compression would run unattributed "
              "and your dashboard would show nothing. Refusing to run.")
        return 1

    task = load_task(args.task)
    arm = ARMS[args.arm]
    workdir = prepare_workdir(task)
    session_path = Path(args.sessions_dir) / f"{task.id}__{arm.name}.jsonl"

    # A campaign chains several bugs, so it needs more turns to finish. Give it a
    # floor of 40 unless the caller asked for more.
    max_turns = max(args.max_turns, 40) if task.is_campaign else args.max_turns

    print(f"task    : {task.id} — {task.title}")
    print(f"arm     : {arm.name}  (kind={arm.use_kind} intent={arm.use_intent} guard={arm.use_guard})")
    print(f"workdir : {workdir}\n")

    cache = CompressionCache("segpilot_cache.db")
    try:
        agent = RuffAgent(
            workdir=workdir, task=task, config=cfg, arm=arm,
            compressor=SegpilotCompressor(cfg, cache=cache),
            upstream=GeminiUpstream(cfg.upstream),
            session_path=session_path, verbose=not args.quiet,
        )
        result = agent.run(max_turns=max_turns)
    finally:
        cache.close()
        if not args.keep_workdir:
            shutil.rmtree(workdir, ignore_errors=True)

    print(f"\n{'=' * 62}")
    print(f"  solved        : {result.solved}   ({result.stop_reason})")
    print(f"  turns         : {result.turns}")
    print(f"  context       : {result.original_tokens} -> {result.compressed_tokens} "
          f"tokens (saved {result.saved_tokens}, ratio {result.ratio:.3f})")
    print(f"  billed prompt : {result.billed_prompt_tokens}")
    print(f"  wall          : {result.wall_s}s")
    print(f"  session       : {result.session_path}")
    print("=" * 62)
    return 0 if result.solved else 2


if __name__ == "__main__":
    sys.exit(main())
