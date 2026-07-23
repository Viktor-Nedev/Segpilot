"""Task loading and sandboxed execution for the seeded-bug benchmark.

Each task takes the clean `bench/project/` codebase, copies it to a scratch
directory, applies a one-line seed that introduces the bug, and lets the agent
work there. The repository itself is never mutated, so runs are independent and
repeatable.

Success is decided by pytest, not by an LLM judge.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

BENCH_DIR = Path(__file__).parent
PROJECT_DIR = BENCH_DIR / "project"
TASKS_DIR = BENCH_DIR / "tasks"


class TaskError(RuntimeError):
    pass


@dataclass
class Task:
    id: str
    title: str
    instruction: str
    seeds: list[dict]
    test_command: list[str]
    ground_truth: dict

    @property
    def is_campaign(self) -> bool:
        """A campaign chains several bugs in one session, so the agent's context
        drifts across sub-tasks -- the condition SEGPILOT's intent routing is
        actually about. Single-bug tasks are short and do not drift."""
        return len(self.seeds) > 1

    @property
    def must_appear(self) -> list[str]:
        """Strings that must survive compression for the task to be solvable.

        Chosen from the ground truth, not discovered at runtime, so
        task-relevant retention is an objective measure rather than something
        we tuned after seeing results.
        """
        return list(self.ground_truth.get("must_appear", []))


def load_task(task_id: str) -> Task:
    path = TASKS_DIR / f"{task_id}.json"
    if not path.exists():
        available = sorted(p.stem for p in TASKS_DIR.glob("*.json"))
        raise TaskError(f"unknown task {task_id!r}. Available: {available}")
    data = json.loads(path.read_text(encoding="utf-8"))
    # A task carries either one `seed` (single-bug) or a list of `seeds`
    # (campaign). Normalise to a list so the rest of the harness is uniform.
    seeds = data.get("seeds") or ([data["seed"]] if "seed" in data else [])
    if not seeds:
        raise TaskError(f"{task_id}: task defines neither `seed` nor `seeds`")
    return Task(
        id=data["id"],
        title=data["title"],
        instruction=data["instruction"],
        seeds=seeds,
        test_command=data.get("test_command", ["-m", "pytest", "-q"]),
        ground_truth=data.get("ground_truth", {}),
    )


def list_tasks() -> list[str]:
    return sorted(p.stem for p in TASKS_DIR.glob("*.json"))


def _apply_seed(workdir: Path, seed: dict, task_id: str) -> None:
    """Introduce one bug via an unambiguous find/replace."""
    target = workdir / seed["file"]
    if not target.exists():
        raise TaskError(f"{task_id}: seed target missing: {seed['file']}")
    source = target.read_text(encoding="utf-8")
    find = seed["find"]
    if find not in source:
        raise TaskError(
            f"{task_id}: seed pattern not found in {seed['file']}. The project "
            f"changed and the task needs updating.\nPattern: {find!r}"
        )
    if source.count(find) != 1:
        raise TaskError(
            f"{task_id}: seed pattern is ambiguous in {seed['file']} "
            f"({source.count(find)} matches); make it more specific"
        )
    target.write_text(source.replace(find, seed["replace"]), encoding="utf-8")


def prepare_workdir(task: Task, *, parent: str | Path | None = None) -> Path:
    """Copy the clean project to a scratch dir and introduce every seeded bug.

    For a campaign this applies several seeds, so the agent must work through
    multiple bugs in one session -- that is what makes the context accumulate
    and drift.
    """
    workdir = Path(tempfile.mkdtemp(prefix=f"segpilot_{task.id}_", dir=parent))
    shutil.copytree(PROJECT_DIR, workdir, dirs_exist_ok=True)
    for seed in task.seeds:
        _apply_seed(workdir, seed, task.id)
    return workdir


def run_tests(workdir: Path, task: Task, *, timeout: float = 120.0) -> tuple[bool, str]:
    """Run the task's tests. Returns (passed, combined output)."""
    try:
        proc = subprocess.run(
            [sys.executable, *task.test_command],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"TIMEOUT after {timeout}s"
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, output


def verify_task(task: Task) -> None:
    """Sanity-check a task: clean project passes, seeded project fails.

    A task whose seed does not actually break the tests would silently report
    100% success for every arm and quietly poison the benchmark.
    """
    clean = Path(tempfile.mkdtemp(prefix=f"verify_clean_{task.id}_"))
    try:
        shutil.copytree(PROJECT_DIR, clean, dirs_exist_ok=True)
        passed, out = run_tests(clean, task)
        if not passed:
            raise TaskError(f"{task.id}: clean project FAILS its own tests:\n{out[-800:]}")
    finally:
        shutil.rmtree(clean, ignore_errors=True)

    seeded = prepare_workdir(task)
    try:
        passed, out = run_tests(seeded, task)
        if passed:
            raise TaskError(
                f"{task.id}: seeded bug does NOT break the tests -- the task is a no-op"
            )
    finally:
        shutil.rmtree(seeded, ignore_errors=True)


if __name__ == "__main__":
    failures = 0
    for task_id in list_tasks():
        task = load_task(task_id)
        try:
            verify_task(task)
            print(f"  OK    {task_id}  {task.title}")
        except TaskError as exc:
            failures += 1
            print(f"  BROKEN {task_id}: {exc}")
    print(f"\n{len(list_tasks()) - failures}/{len(list_tasks())} tasks valid")
    sys.exit(1 if failures else 0)
